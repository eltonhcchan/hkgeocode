/**
 * Esri REST polygon layer → HKGeoCode cells by centroid containment.
 */
(function (root) {
  "use strict";

  const SKIP_FIELD_TYPES = {
    esriFieldTypeGeometry: true,
    esriFieldTypeBlob: true,
    esriFieldTypeRaster: true,
    esriFieldTypeXML: true,
  };
  const SKIP_FIELD_NAMES = {
    shape: true,
    shape_length: true,
    shape_area: true,
    st_area_shape: true,
    st_length_shape: true,
  };
  const PALETTE = [
    "#e41a1c",
    "#377eb8",
    "#4daf4a",
    "#984ea3",
    "#ff7f00",
    "#a65628",
    "#f781bf",
    "#66c2a5",
    "#fc8d62",
    "#8da0cb",
    "#e78ac3",
    "#a6d854",
    "#ffd92f",
    "#e5c494",
    "#b3b3b3",
  ];

  function normalizeLayerUrl(raw) {
    let url = String(raw || "").trim();
    if (!url) {
      throw new Error("Enter an Esri REST feature layer URL.");
    }
    url = url.split("?")[0].replace(/\/+$/, "");
    url = url.replace(/\/query$/i, "");
    if (!/\/(FeatureServer|MapServer)\/\d+$/i.test(url)) {
      throw new Error(
        "URL must be a layer path ending in FeatureServer/n or MapServer/n."
      );
    }
    return url;
  }

  function isPolygonGeometry(type) {
    return type === "esriGeometryPolygon";
  }

  function classificationFields(fields) {
    return (fields || []).filter((field) => {
      const name = String(field.name || "");
      if (!name || SKIP_FIELD_TYPES[field.type]) {
        return false;
      }
      if (SKIP_FIELD_NAMES[name.toLowerCase()]) {
        return false;
      }
      return true;
    });
  }

  function colorForValue(value, colorByValue) {
    const key = value == null || value === "" ? "(empty)" : String(value);
    if (!colorByValue.has(key)) {
      colorByValue.set(key, PALETTE[colorByValue.size % PALETTE.length]);
    }
    return { key, color: colorByValue.get(key) };
  }

  function ringArea(ring) {
    let area = 0;
    for (let i = 0; i < ring.length - 1; i += 1) {
      area += ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1];
    }
    return area / 2;
  }

  function closeRing(ring) {
    if (!ring.length) {
      return ring;
    }
    const first = ring[0];
    const last = ring[ring.length - 1];
    if (first[0] !== last[0] || first[1] !== last[1]) {
      return ring.concat([first]);
    }
    return ring;
  }

  function ringsToGeometry(rings) {
    const polygons = [];
    let current = null;
    rings.forEach((raw) => {
      const coords = closeRing(raw.map((p) => [p[0], p[1]]));
      if (coords.length < 4) {
        return;
      }
      const outer = ringArea(coords) < 0;
      if (outer || !current) {
        current = [coords];
        polygons.push(current);
      } else {
        current.push(coords);
      }
    });
    if (!polygons.length) {
      return null;
    }
    if (polygons.length === 1) {
      return { type: "Polygon", coordinates: polygons[0] };
    }
    return { type: "MultiPolygon", coordinates: polygons };
  }

  function esriFeatureToGeoJSON(feature) {
    if (!feature || !feature.geometry || !feature.geometry.rings) {
      return null;
    }
    const geometry = ringsToGeometry(feature.geometry.rings);
    if (!geometry) {
      return null;
    }
    return {
      type: "Feature",
      properties: feature.attributes || {},
      geometry,
    };
  }

  function pointInRing(lng, lat, ring) {
    let inside = false;
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i, i += 1) {
      const xi = ring[i][0];
      const yi = ring[i][1];
      const xj = ring[j][0];
      const yj = ring[j][1];
      const intersect =
        yi > lat !== yj > lat &&
        lng < ((xj - xi) * (lat - yi)) / (yj - yi || 1e-15) + xi;
      if (intersect) {
        inside = !inside;
      }
    }
    return inside;
  }

  function pointInPolygonCoords(lng, lat, rings) {
    if (!rings || !rings.length || !pointInRing(lng, lat, rings[0])) {
      return false;
    }
    for (let i = 1; i < rings.length; i += 1) {
      if (pointInRing(lng, lat, rings[i])) {
        return false;
      }
    }
    return true;
  }

  function pointInGeometry(lng, lat, geometry) {
    if (!geometry) {
      return false;
    }
    if (geometry.type === "Polygon") {
      return pointInPolygonCoords(lng, lat, geometry.coordinates);
    }
    if (geometry.type === "MultiPolygon") {
      return geometry.coordinates.some((rings) =>
        pointInPolygonCoords(lng, lat, rings)
      );
    }
    return false;
  }

  async function fetchJson(url) {
    const response = await fetch(url);
    if (!response.ok) {
      throw new Error(`Layer request failed (HTTP ${response.status}).`);
    }
    const data = await response.json();
    if (data.error) {
      const message = data.error.message || data.error.details || "Esri REST error";
      throw new Error(String(message));
    }
    return data;
  }

  async function inspectLayer(rawUrl) {
    const url = normalizeLayerUrl(rawUrl);
    const info = await fetchJson(`${url}?f=json`);
    if (!isPolygonGeometry(info.geometryType)) {
      throw new Error(
        `Layer is ${info.geometryType || "not a polygon feature"}. Provide a polygon FeatureServer/MapServer layer.`
      );
    }
    const fields = classificationFields(info.fields);
    if (!fields.length) {
      throw new Error("Layer has no usable attribute fields for classification.");
    }
    return {
      url,
      name: info.name || url,
      geometryType: info.geometryType,
      maxRecordCount: info.maxRecordCount || 1000,
      advancedQueryCapabilities: info.advancedQueryCapabilities || {},
      fields,
    };
  }

  async function queryPolygons(layer, bounds) {
    const pageSize = Math.min(1000, layer.maxRecordCount || 1000);
    const supportsOffset =
      layer.advancedQueryCapabilities.supportsPagination !== false;
    const features = [];
    let offset = 0;
    let useGeoJson = true;

    while (true) {
      const params = new URLSearchParams({
        where: "1=1",
        geometry: `${bounds.west},${bounds.south},${bounds.east},${bounds.north}`,
        geometryType: "esriGeometryEnvelope",
        inSR: "4326",
        spatialRel: "esriSpatialRelIntersects",
        outFields: "*",
        returnGeometry: "true",
        outSR: "4326",
        resultRecordCount: String(pageSize),
        f: useGeoJson ? "geojson" : "json",
      });
      if (supportsOffset) {
        params.set("resultOffset", String(offset));
      }
      let data;
      try {
        data = await fetchJson(`${layer.url}/query?${params.toString()}`);
      } catch (err) {
        if (useGeoJson) {
          useGeoJson = false;
          continue;
        }
        throw err;
      }

      const asCollection = useGeoJson && data.type === "FeatureCollection";
      if (asCollection) {
        (data.features || []).forEach((feature) => {
          if (
            feature &&
            feature.geometry &&
            (feature.geometry.type === "Polygon" ||
              feature.geometry.type === "MultiPolygon")
          ) {
            features.push(feature);
          }
        });
        const count = (data.features || []).length;
        if (!supportsOffset || count < pageSize || data.exceededTransferLimit !== true) {
          break;
        }
        offset += count;
        continue;
      }

      useGeoJson = false;
      (data.features || []).forEach((feature) => {
        const geo = esriFeatureToGeoJSON(feature);
        if (geo) {
          features.push(geo);
        }
      });
      const count = (data.features || []).length;
      if (!supportsOffset || count < pageSize || data.exceededTransferLimit !== true) {
        break;
      }
      offset += count;
      if (features.length >= 4000) {
        break;
      }
    }
    return features;
  }

  function assignCells(features, field, codes, cellCentroidLngLat, colorByValue) {
    const colors = colorByValue || new Map();
    const fillByCode = {};
    let assigned = 0;
    codes.forEach((code) => {
      const [lng, lat] = cellCentroidLngLat(code);
      for (let i = 0; i < features.length; i += 1) {
        const feature = features[i];
        if (pointInGeometry(lng, lat, feature.geometry)) {
          const value = feature.properties ? feature.properties[field] : null;
          const { key, color } = colorForValue(value, colors);
          fillByCode[code] = { color, value: key };
          assigned += 1;
          break;
        }
      }
    });
    return { fillByCode, colorByValue: colors, assigned };
  }

  root.HKGPolygonCells = {
    PALETTE,
    normalizeLayerUrl,
    inspectLayer,
    queryPolygons,
    assignCells,
    colorForValue,
    pointInGeometry,
  };
})(typeof window !== "undefined" ? window : globalThis);
