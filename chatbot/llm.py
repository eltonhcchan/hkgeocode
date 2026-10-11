"""Chat agents: an OpenAI-compatible tool-calling agent and a rule-based fallback."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from .tools import Artefact, Session, ToolResult, openai_tool_schemas, parse_length, run_tool

MAX_TOOL_ROUNDS = 6
MAX_TOOL_TEXT = 6000
HISTORY_LIMIT = 30

SYSTEM_PROMPT = """You are a geospatial analyst assistant for Hong Kong open data.

Context
- Point layers come from the CSDI portal (portal.csdi.gov.hk) as ArcGIS FeatureServer layers in EPSG:2326 (HK1980 Grid).
- Points are binned onto HKGeoCode (香港地理碼) cells: 2 characters = 2 km district cell, 4 characters = 100 m neighbourhood cell, 6 characters = 5 m cell. Codes use Crockford Base32 (no I, L, O, U). Example: WG73JD.
- Two layers are loaded into slots A and B. Spatial correlation uses per-cell counts (Pearson/Spearman), Jaccard overlap of occupied cells, bivariate Moran's I with local HH/LL/HL/LH clusters, and nearest-neighbour distances against a random baseline.

Rules
- Always use the tools to get numbers; never invent statistics. Call several tools if a question needs them.
- When the user asks for a chart, histogram, bar chart, map or table, call the matching tool; the figure is shown automatically, so just describe what it shows in a few sentences.
- Quote exact figures from tool output and name HKGeoCode cells when relevant.
- Be concise and concrete. Use plain prose or short bullet lists; no headings.
- If a field name is unclear, call describe_dataset first. Field shortcuts exist: district, venue type, provider, hotspots, name.
- If no layers are loaded, tell the user to load two layers from the sidebar (or call load_layers with CSDI FeatureServer URLs).

Session state
{context}
"""


class AgentError(RuntimeError):
    """Raised when the LLM backend cannot be used."""


@dataclass
class AgentReply:
    text: str
    artefacts: list[Artefact] = field(default_factory=list)
    tool_calls: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# OpenAI-compatible agent
# --------------------------------------------------------------------------- #
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_MODEL = "openai/gpt-4o-mini"


def default_api_key() -> str:
    return os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY", "")


def default_base_url() -> str:
    return os.environ.get("OPENROUTER_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or OPENROUTER_BASE_URL


def default_model() -> str:
    return os.environ.get("OPENROUTER_MODEL") or os.environ.get("OPENAI_MODEL") or DEFAULT_MODEL


class OpenAIAgent:
    """Tool-calling agent for OpenRouter (default) or any OpenAI-compatible endpoint.

    Configuration: ``OPENROUTER_API_KEY`` (required), ``OPENROUTER_MODEL``
    (default ``openai/gpt-4o-mini``; any OpenRouter model id that supports tool
    calling, e.g. ``anthropic/claude-3.5-sonnet``), ``OPENROUTER_BASE_URL``
    (default https://openrouter.ai/api/v1). ``OPENAI_*`` variables are accepted
    as fallbacks so other OpenAI-compatible servers (Ollama, LM Studio) still work.
    """

    def __init__(self, session: Session, api_key: str | None = None, base_url: str | None = None, model: str | None = None):
        self.session = session
        self.api_key = api_key or default_api_key()
        self.base_url = base_url or default_base_url()
        self.model = model or default_model()
        if not self.api_key:
            raise AgentError("OPENROUTER_API_KEY is not set")
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover
            raise AgentError("the 'openai' package is not installed (pip install openai)") from exc
        headers = {}
        if "openrouter.ai" in self.base_url:
            # Optional OpenRouter attribution headers (shown in their usage dashboard).
            headers = {"HTTP-Referer": "https://github.com/eltonhcchan/hkgeocode", "X-Title": "CSDI x HKGeoCode chatbot"}
        self.client = OpenAI(
            api_key=self.api_key, base_url=self.base_url, default_headers=headers, max_retries=1, timeout=120
        )
        self.history: list[dict] = []

    @property
    def label(self) -> str:
        host = "OpenRouter" if "openrouter.ai" in self.base_url else self.base_url
        return f"{self.model} via {host}"

    def reset(self) -> None:
        self.history.clear()

    def respond(self, user_text: str) -> AgentReply:
        from openai import OpenAIError

        self.history.append({"role": "user", "content": user_text})
        self.history = self.history[-HISTORY_LIMIT:]
        messages = [{"role": "system", "content": SYSTEM_PROMPT.format(context=self.session.context_text())}] + self.history
        artefacts: list[Artefact] = []
        calls: list[str] = []
        tools = openai_tool_schemas()

        for _ in range(MAX_TOOL_ROUNDS):
            try:
                completion = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=0.2,
                )
            except OpenAIError as exc:
                raise AgentError(f"LLM request failed: {exc}") from exc
            msg = completion.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None) or []
            assistant_entry = {"role": "assistant", "content": msg.content or ""}
            if tool_calls:
                assistant_entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"},
                    }
                    for tc in tool_calls
                ]
            messages.append(assistant_entry)
            self.history.append(assistant_entry)
            if not tool_calls:
                return AgentReply(msg.content or "", artefacts, calls)
            for tc in tool_calls:
                name = tc.function.name
                result = run_tool(self.session, name, tc.function.arguments)
                calls.append(f"{name}({tc.function.arguments})")
                artefacts.extend(result.artefacts)
                tool_msg = {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result.text[:MAX_TOOL_TEXT],
                }
                messages.append(tool_msg)
                self.history.append(tool_msg)
        return AgentReply(
            "I ran several analysis steps; the results are shown above. Ask a follow-up for more detail.",
            artefacts,
            calls,
        )


# --------------------------------------------------------------------------- #
# Rule-based agent (no LLM)
# --------------------------------------------------------------------------- #
HELP_TEXT = """I can run these analyses on the loaded layers (no language model is configured, so I match keywords):

- "summary" or "quantify" - points per HKGeoCode cell for both layers
- "correlation" or "moran" - spatial correlation measures and interpretation
- "map" - interactive map (add "2 km" or "100 m" to pick the resolution)
- "histogram of wifi" / "histogram of hotspots for wifi" - count or field histograms
- "bar chart of wifi by venue type" / "by district" / "by provider"
- "top cells for bus stops" - busiest cells
- "which cells have bus stops but no wifi" - gap cells (or "wifi but no bus", "both")
- "how many wifi in Yuen Long" - filter a layer by a field value
- "fields of wifi" - describe a dataset
- "what is in cell H9GB" - contents of one HKGeoCode cell
- "insights" or "what should we do next" - interpretation and suggested actions
- "use 2 km" / "use 100 m" - change the default resolution
"""

# An upper-case HKGeoCode introduced by "cell", "code", "in", "inside" or "at".
_CODE_RE = re.compile(r"(?:\bcell|\bcode|\bhkgeocode|\bin|\binside|\bat)\s+([0-9A-HJKMNP-TV-Z]{2}(?:[0-9A-HJKMNP-TV-Z]{2}){0,2})\b")


class RuleBasedAgent:
    """Deterministic keyword agent that drives the same tools as the LLM."""

    def __init__(self, session: Session):
        self.session = session

    label = "rule-based (no LLM)"

    def reset(self) -> None:  # pragma: no cover - nothing to reset
        pass

    # -- helpers -----------------------------------------------------------
    def _layer_key(self, text: str) -> str:
        t = text.lower()
        if re.search(r"\blayer\s*a\b|\(a\)", t):
            return "A"
        if re.search(r"\blayer\s*b\b|\(b\)", t):
            return "B"
        hits = {}
        for key, lyr in self.session.layers.items():
            tokens = [tok for tok in re.split(r"[\s\-_.()/]+", lyr.name.lower()) if len(tok) > 2]
            tokens += [tok.rstrip("s") for tok in tokens]
            if key == "B" or "wifi" in lyr.name.lower().replace("-", "") or "wi-fi" in lyr.name.lower():
                tokens += ["wifi", "wi-fi", "hotspot"]
            if "bus" in lyr.name.lower():
                tokens += ["bus", "stop", "stops"]
            score = sum(1 for tok in set(tokens) if tok and re.search(rf"\b{re.escape(tok)}", t))
            if score:
                hits[key] = score
        if not hits:
            return "both"
        return max(hits, key=hits.get)

    @staticmethod
    def _field(text: str) -> str | None:
        stop = r"(?:\s+(?:for|of|in|on|weighted|summed|sum)\b|[?.,]|$)"
        m = re.search(r"\bby\s+(?:the\s+)?([a-z0-9_ ]+?)" + stop, text, re.I)
        if m:
            return m.group(1).strip()
        m = re.search(r"\b(?:histogram|distribution)\s+of\s+(?:the\s+)?([a-z0-9_ ]+?)" + stop, text, re.I)
        if m:
            cand = m.group(1).strip()
            if cand.lower() not in ("counts", "count", "points", "cells", "cell counts", "points per cell"):
                return cand
        return None

    def _reply(self, name: str, **kwargs) -> AgentReply:
        res: ToolResult = run_tool(self.session, name, kwargs)
        text = res.text
        # Tool text is written for an LLM; aligned tables need a monospace block in the chat UI.
        if re.search(r"^\S.*\s{2,}\S", text, re.M):
            text = f"```text\n{text}\n```"
        return AgentReply(text, res.artefacts, [f"{name}({json.dumps(kwargs)})"])

    # -- main entry ----------------------------------------------------------
    def respond(self, user_text: str) -> AgentReply:
        text = user_text.strip()
        t = text.lower()
        length = parse_length(t)

        if re.search(r"\b(help|what can you do|commands)\b", t):
            return AgentReply(HELP_TEXT)

        if re.search(r"\b(search|find|look up)\b", t) and re.search(r"csdi|dataset|layer|portal", t):
            keyword = re.sub(
                r"^(?:\s*(?:search|find|look up|the|a|an|csdi|portal|datasets?|layers?|for|about|on|called|named|point))+\s*",
                "",
                text,
                flags=re.I,
            ).strip(" \"'?.")
            if keyword:
                return self._reply("search_csdi", keyword=keyword)

        if re.search(r"\b(use|set|switch to)\b.*\b(resolution|km|\bm\b|metre|meter|district|neighbou?rhood)", t) and length:
            return self._reply("set_resolution", length=length)

        if not self.session.layers:
            return AgentReply("No layers are loaded yet. Pick two CSDI point layers in the sidebar and click Run analysis.")

        if re.search(r"\bmap\b", t):
            return self._reply("show_map", length=length, show_points=not re.search(r"without points|no points", t))

        if re.search(r"\b(insight|recommend|suggest|next step|what should|action|further)", t):
            return self._reply("insights", length=length)

        if re.search(r"correlat|moran|relationship|associat|co-?locat|overlap|spatial(ly)? (related|correlated)|nearest", t):
            return self._reply("correlate", length=length)

        if re.search(r"\b(but no|without|no\s+\w+\s+(?:nearby|around)|only|gap)", t) and self.session.ready:
            kind = "both"
            m2 = re.search(r"(.+?)\s+(?:but no|without|and no)\s+(.+)", t)
            if m2:
                first = self._layer_key(m2.group(1))
                kind = "A only" if first == "A" else "B only" if first == "B" else "A only"
            elif re.search(r"\bboth\b|shared", t):
                kind = "both"
            else:
                key = self._layer_key(t)
                kind = {"A": "A only", "B": "B only"}.get(key, "both")
            return self._reply("compare_cells", kind=kind, length=length)

        cm = _CODE_RE.search(text)
        if cm and re.search(r"\bcell\b|hkgeocode|what('s| is) in|inside|contents", t):
            return self._reply("cell_lookup", code=cm.group(1))

        if re.search(r"\b(fields?|columns?|schema|describe|attributes?|what data)\b", t):
            key = self._layer_key(t)
            return self._reply("describe_dataset", layer=key if key != "both" else "A")

        if re.search(r"\bbar\b|bar chart|barchart|breakdown|by (venue|district|provider|operator|type|category|area)", t):
            key = self._layer_key(t)
            fld = self._field(text) or "district"
            m3 = re.search(r"(?:weighted|sum(?:med)?)\s+(?:by|of)\s+([a-z0-9_ ]+)", t)
            return self._reply("bar_chart", layer=key if key != "both" else "B", field=fld, weight=m3.group(1).strip() if m3 else None)

        if re.search(r"histogram|distribution", t):
            key = self._layer_key(t)
            fld = self._field(text)
            return self._reply("histogram", layer=key, field=fld, length=length)

        if re.search(r"top\s+(\d+\s+)?cells|busiest|densest|most (points|stops|hotspots)|hot ?spots? cells", t):
            key = self._layer_key(t)
            n = re.search(r"top\s+(\d+)", t)
            return self._reply("top_cells", layer=key, length=length, top_n=int(n.group(1)) if n else 10)

        m4 = re.search(r"how many\b.*?\b(?:in|for|at|within)\s+([A-Za-z][A-Za-z '\-]+?)(?:\?|$|\s+by\b)", text, re.I)
        if m4 and re.search(r"how many|count", t):
            key = self._layer_key(t)
            key = key if key != "both" else "B"
            value = m4.group(1).strip()
            if value.lower() not in ("total", "each cell", "cells", "hong kong"):
                return self._reply("query_layer", layer=key, field="district", contains=value, length=length)

        if re.search(r"how many|count|values of|list (the )?(values|districts|types)", t):
            key = self._layer_key(t)
            fld = self._field(text)
            if fld:
                return self._reply("field_values", layer=key if key != "both" else "B", field=fld)
            return self._reply("quantify", layer=key, length=length)

        if re.search(r"summar|quantif|overview|statistics|stats|per cell|points per", t):
            return self._reply("quantify", layer=self._layer_key(t), length=length)

        return AgentReply("I did not recognise that request.\n\n" + HELP_TEXT)


def make_agent(session: Session, prefer_llm: bool = True, **llm_kwargs) -> tuple[OpenAIAgent | RuleBasedAgent, str | None]:
    """Return (agent, warning). Falls back to the rule-based agent when the LLM is unavailable."""
    if prefer_llm:
        try:
            return OpenAIAgent(session, **llm_kwargs), None
        except AgentError as exc:
            return RuleBasedAgent(session), str(exc)
    return RuleBasedAgent(session), None
