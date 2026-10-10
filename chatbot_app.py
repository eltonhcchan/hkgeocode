#!/usr/bin/env python3
"""Streamlit chatbot: two CSDI point layers on the HKGeoCode grid.

Run with:

    streamlit run chatbot_app.py

Set OPENROUTER_API_KEY (and optionally OPENROUTER_MODEL) to chat through an
OpenRouter model with tool calling; any other OpenAI-compatible endpoint works
via OPENAI_BASE_URL / OPENAI_API_KEY. Without a key the app uses a
keyword-driven rule-based agent that calls the same analysis tools.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

sys.path.insert(0, str(Path(__file__).resolve().parent))

from chatbot.csdi import DEFAULT_LAYERS, CSDIError, search_catalogue  # noqa: E402
from chatbot.grid import LEVEL_LABEL  # noqa: E402
from chatbot.llm import (  # noqa: E402
    AgentError,
    AgentReply,
    RuleBasedAgent,
    default_api_key,
    default_base_url,
    default_model,
    make_agent,
)
from chatbot.tools import Artefact, Session, run_tool  # noqa: E402

st.set_page_config(page_title="CSDI x HKGeoCode chatbot", page_icon=":world_map:", layout="wide")

RESOLUTIONS = {"100 m neighbourhood cell (4 chars)": 4, "2 km district cell (2 chars)": 2}
SLOT_DEFAULTS = {
    "A": ("Coordinates of Bus Stops", DEFAULT_LAYERS["Coordinates of Bus Stops"]),
    "B": ("Wi-Fi.HK", DEFAULT_LAYERS["Wi-Fi.HK"]),
}


# --------------------------------------------------------------------------- #
# State
# --------------------------------------------------------------------------- #
def _session() -> Session:
    if "session" not in st.session_state:
        st.session_state.session = Session(output_dir=Path(__file__).resolve().parent / "output")
    return st.session_state.session


def _agent():
    """(Re)build the agent when LLM settings change."""
    cfg = (
        st.session_state.get("use_llm", True),
        st.session_state.get("api_key", ""),
        st.session_state.get("base_url", ""),
        st.session_state.get("model", ""),
    )
    if st.session_state.get("agent_cfg") != cfg or "agent" not in st.session_state:
        use_llm, api_key, base_url, model = cfg
        agent, warning = make_agent(
            _session(),
            prefer_llm=use_llm,
            api_key=api_key or None,
            base_url=base_url or None,
            model=model or None,
        )
        st.session_state.agent = agent
        st.session_state.agent_warning = warning
        st.session_state.agent_cfg = cfg
    return st.session_state.agent


def _messages() -> list[dict]:
    return st.session_state.setdefault("messages", [])


def _post(role: str, text: str, artefacts: list[Artefact] | None = None, tool_calls: list[str] | None = None) -> None:
    _messages().append({"role": role, "text": text, "artefacts": artefacts or [], "tool_calls": tool_calls or []})


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #
def render_artefact(a: Artefact) -> None:
    if a.kind == "plotly":
        st.plotly_chart(a.payload, use_container_width=True)
    elif a.kind == "html":
        components.html(a.payload, height=620, scrolling=False)
    elif a.kind == "dataframe":
        if a.title:
            st.caption(a.title)
        st.dataframe(a.payload, use_container_width=True, hide_index=True)
    elif a.kind == "markdown":
        st.markdown(a.payload)


def render_message(msg: dict) -> None:
    with st.chat_message(msg["role"]):
        if msg["text"]:
            st.markdown(msg["text"])
        for a in msg["artefacts"]:
            render_artefact(a)
        if msg["tool_calls"]:
            with st.expander("Tools used", expanded=False):
                for c in msg["tool_calls"]:
                    st.code(c, language="text")


# --------------------------------------------------------------------------- #
# Sidebar: layer pickers and settings
# --------------------------------------------------------------------------- #
def layer_picker(slot: str) -> tuple[str, str]:
    """Keyword search box + results dropdown for one layer slot. Returns (name, url)."""
    default_name, default_url = SLOT_DEFAULTS[slot]
    st.markdown(f"**Layer {slot}**")
    kw_key, res_key, pick_key = f"kw_{slot}", f"results_{slot}", f"pick_{slot}"
    keyword = st.text_input("Search CSDI", value=default_name, key=kw_key, label_visibility="collapsed", placeholder="CSDI keyword")
    if st.button("Search", key=f"btn_{slot}", use_container_width=True):
        with st.spinner(f"Searching CSDI for {keyword!r}..."):
            try:
                items = search_catalogue(keyword, max_results=10)
            except Exception as exc:  # network errors
                st.error(f"Search failed: {exc}")
                items = []
        st.session_state[res_key] = [(it.label, it.layer_url) for it in items]
        if not items:
            st.warning("No point layers found.")
    options = st.session_state.get(res_key) or [(default_name, default_url)]
    labels = [o[0] for o in options]
    choice = st.selectbox("Point layer", labels, key=pick_key, label_visibility="collapsed")
    url = dict(options)[choice]
    custom = st.text_input("or FeatureServer URL", value="", key=f"url_{slot}", placeholder="paste a FeatureServer layer URL")
    if custom.strip():
        url = custom.strip()
        choice = custom.strip().rstrip("/").split("/")[-3] if "/" in custom else custom
    st.caption(url)
    return choice.split(" (")[0], url


def run_analysis(name_a: str, url_a: str, name_b: str, url_b: str, length: int) -> None:
    session = _session()
    session.length = length
    progress = st.progress(0, text="Loading layers from CSDI...")

    def cb(slot):
        def _p(done, total):
            frac = min(done / total, 1.0) if total else 0.5
            progress.progress(0.1 + 0.3 * frac + (0.0 if slot == "A" else 0.3), text=f"Layer {slot}: {done:,}/{total:,} features")

        return _p

    try:
        session.load("A", url_a, name_a, progress=cb("A"))
        session.load("B", url_b, name_b, progress=cb("B"))
    except (CSDIError, ValueError) as exc:
        progress.empty()
        st.error(f"Could not load layers: {exc}")
        return
    progress.progress(0.75, text="Binning onto HKGeoCode cells and computing spatial correlation...")
    quant = run_tool(session, "quantify", {"layer": "both", "length": length})
    corr = run_tool(session, "correlate", {"length": length})
    progress.progress(0.9, text="Rendering map...")
    mp = run_tool(session, "show_map", {"length": length})
    progress.empty()

    a, b = session.layers["A"], session.layers["B"]
    r = session.correlation(length)
    summary = (
        f"**Loaded** layer A = {a.name} ({len(a.df):,} points) and layer B = {b.name} ({len(b.df):,} points) "
        f"from CSDI, binned to {LEVEL_LABEL[length]}s.\n\n"
        f"**Quantification**\n\n```\n{quant.text}\n```\n\n"
        f"**Spatial correlation**\n\n```\n{r.to_text()}\n```\n\n"
        f"**Interpretation.** {r.interpret()}\n\n"
        "Ask me about the datasets: histograms, bar charts by any field, gap cells, cell contents, or what to do next."
    )
    artefacts = [mp.artefacts[0]] + [x for x in quant.artefacts if x.kind == "plotly"] + corr.artefacts
    st.session_state.messages = []
    _post("assistant", summary, artefacts, ["load_layers", "quantify", "correlate", "show_map"])
    agent = st.session_state.get("agent")
    if agent is not None:
        agent.reset()
    st.session_state.analysis_done = True


with st.sidebar:
    st.title("CSDI x HKGeoCode")
    st.caption("Pick two CSDI point layers, bin them onto the HKGeoCode grid, and chat about the result.")
    name_a, url_a = layer_picker("A")
    st.divider()
    name_b, url_b = layer_picker("B")
    st.divider()
    res_label = st.selectbox("Analysis resolution", list(RESOLUTIONS), index=0)
    length = RESOLUTIONS[res_label]
    perms = st.select_slider("Moran permutations", options=[99, 199, 499, 999], value=499)
    _session().permutations = perms
    run = st.button("Run analysis", type="primary", use_container_width=True)

    st.divider()
    st.markdown("**Language model (OpenRouter)**")
    st.checkbox("Use LLM agent", value=bool(default_api_key()), key="use_llm")
    st.text_input("OpenRouter API key", value=default_api_key(), type="password", key="api_key", help="OPENROUTER_API_KEY")
    st.text_input(
        "Model",
        value=default_model(),
        key="model",
        help="Any OpenRouter model id with tool calling, e.g. openai/gpt-4o-mini, anthropic/claude-3.5-sonnet, google/gemini-2.0-flash-001",
    )
    st.text_input("Base URL", value=default_base_url(), key="base_url", help="Change only for a non-OpenRouter OpenAI-compatible server")
    agent = _agent()
    if st.session_state.get("agent_warning"):
        st.warning(f"LLM unavailable ({st.session_state.agent_warning}); using the rule-based agent.")
    st.caption(f"Agent: {agent.label}")
    if st.button("Clear chat", use_container_width=True):
        st.session_state.messages = []
        agent.reset()
        st.rerun()

if run:
    run_analysis(name_a, url_a, name_b, url_b, length)


# --------------------------------------------------------------------------- #
# Main panel
# --------------------------------------------------------------------------- #
st.title("CSDI point layers on the HKGeoCode grid")
session = _session()
if not session.layers:
    st.info(
        "Choose two CSDI point layers in the sidebar (defaults: Bus Stops and Wi-Fi.HK) and click **Run analysis**. "
        "The app downloads the layers, bins them onto HKGeoCode cells, measures their spatial correlation and draws a map; "
        "then you can ask questions, request histograms and bar charts, and get suggestions for further action."
    )
else:
    cols = st.columns(4)
    a = session.layers.get("A")
    b = session.layers.get("B")
    cols[0].metric(f"A: {a.name}" if a else "A", f"{len(a.df):,}" if a else "-", "points")
    cols[1].metric(f"B: {b.name}" if b else "B", f"{len(b.df):,}" if b else "-", "points")
    if session.ready and session.length in session.correlations:
        r = session.correlations[session.length]
        cols[2].metric("Shared cells", f"{r.cells_both:,}", f"Jaccard {r.jaccard:.2f}")
        cols[3].metric("Bivariate Moran's I", f"{r.moran['I']:.3f}", f"p = {r.moran['p_value']:.3f}")

for msg in _messages():
    render_message(msg)

prompt = st.chat_input("Ask about the layers, e.g. 'bar chart of Wi-Fi by venue type' or 'which cells have bus stops but no Wi-Fi?'")
if prompt:
    _post("user", prompt)
    render_message(_messages()[-1])
    agent = _agent()
    with st.chat_message("assistant"):
        with st.spinner("Thinking..."):
            try:
                reply: AgentReply = agent.respond(prompt)
            except AgentError as exc:
                fallback = RuleBasedAgent(session)
                reply = fallback.respond(prompt)
                reply.text = f"_LLM error: {exc}. Answered with the rule-based agent._\n\n" + reply.text
        if reply.text:
            st.markdown(reply.text)
        for art in reply.artefacts:
            render_artefact(art)
        if reply.tool_calls:
            with st.expander("Tools used", expanded=False):
                for c in reply.tool_calls:
                    st.code(c, language="text")
    _post("assistant", reply.text, reply.artefacts, reply.tool_calls)
