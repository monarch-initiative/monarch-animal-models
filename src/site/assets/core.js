export const MISSING_VALUE = "__missing__";

const collator = new Intl.Collator(undefined, { sensitivity: "base", numeric: true });
const searchCache = new WeakMap();

export function normalizeText(value) {
  return String(value ?? "")
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase()
    .replace(/[^\p{L}\p{N}]+/gu, " ")
    .trim();
}

function flattenValue(value, output = []) {
  if (value == null || value === "") return output;
  if (Array.isArray(value)) {
    value.forEach((item) => flattenValue(item, output));
  } else if (typeof value === "object") {
    Object.values(value).forEach((item) => flattenValue(item, output));
  } else {
    output.push(String(value));
  }
  return output;
}

export function matchesSearch(record, query, fields) {
  const tokens = normalizeText(query).split(/\s+/).filter(Boolean);
  if (!tokens.length) return true;
  const selectedFields = fields || Object.keys(record);
  const signature = selectedFields.join("\u001f");
  let cache = searchCache.get(record);
  if (!cache) {
    cache = new Map();
    searchCache.set(record, cache);
  }
  let haystack = cache.get(signature);
  if (haystack == null) {
    haystack = normalizeText(
      selectedFields.flatMap((field) => flattenValue(record[field])).join(" "),
    );
    cache.set(signature, haystack);
  }
  return tokens.every((token) => haystack.includes(token));
}

function computeFacetValues(record, field) {
  const raw = record?.[field];
  const values = Array.isArray(raw) ? raw : raw == null || raw === "" ? [] : [raw];
  const normalized = [];
  const seen = new Set();
  for (const value of values) {
    const text = typeof value === "object" ? value?.label || value?.name || value?.id : value;
    if (text == null || String(text).trim() === "") continue;
    const clean = String(text).trim();
    const key = clean.toLocaleLowerCase();
    if (!seen.has(key)) {
      seen.add(key);
      normalized.push(clean);
    }
  }
  return normalized.length ? normalized : [MISSING_VALUE];
}

// Records are immutable once loaded, so each record's normalized values per
// field are computed once. Without this, every facet click re-normalizes every
// value of every record for every facet.
const facetCache = new WeakMap();

function cachedFacet(record, field) {
  if (record == null || typeof record !== "object") {
    const values = computeFacetValues(record, field);
    return { values, set: new Set(values) };
  }
  let fields = facetCache.get(record);
  if (!fields) {
    fields = new Map();
    facetCache.set(record, fields);
  }
  let entry = fields.get(field);
  if (!entry) {
    const values = computeFacetValues(record, field);
    entry = { values, set: new Set(values) };
    fields.set(field, entry);
  }
  return entry;
}

export function facetValues(record, field) {
  return cachedFacet(record, field).values;
}

function valuesForFilter(filters, field) {
  const value = filters instanceof Map ? filters.get(field) : filters?.[field];
  if (value instanceof Set) return [...value];
  return Array.isArray(value) ? value : value == null || value === "" ? [] : [String(value)];
}

function recordMatchesFacet(record, field, selected) {
  if (!selected.length) return true;
  const available = cachedFacet(record, field).set;
  return selected.some((value) => available.has(value));
}

export function applyFilters(records, filters, facetDefs) {
  const definitions = facetDefs || Object.keys(filters || {}).map((field) => ({ field }));
  const active = definitions
    .map(({ field }) => [field, valuesForFilter(filters, field)])
    .filter(([, selected]) => selected.length);
  if (!active.length) return records.slice();
  return records.filter((record) =>
    active.every(([field, selected]) => recordMatchesFacet(record, field, selected)),
  );
}

// Counts are disjunctive: while counting one facet, all other active facets remain
// applied. This lets a user see useful alternatives instead of a wall of zeroes.
export function facetCounts(records, filters, facetDefs, targetField) {
  const otherFacets = (facetDefs || []).filter(({ field }) => field !== targetField);
  const candidates = applyFilters(records, filters, otherFacets);
  const counts = new Map();
  for (const record of candidates) {
    for (const value of cachedFacet(record, targetField).set) {
      counts.set(value, (counts.get(value) || 0) + 1);
    }
  }
  return counts;
}

const NUMERIC_SORTS = {
  counterparts: "counterpart_count",
  dismech_models: "dismech_model_count",
  kg_models: "kg_exact_model_count",
};

function labelForSort(record, sortKey) {
  if (NUMERIC_SORTS[sortKey]) return Number(record[NUMERIC_SORTS[sortKey]]) || 0;
  if (sortKey === "disease") return record.disease_labels?.[0] || record.context_name || "";
  return record.name || "";
}

export function sortRecords(records, sortKey) {
  const descending = Boolean(NUMERIC_SORTS[sortKey]);
  return [...records].sort((left, right) => {
    const a = labelForSort(left, sortKey);
    const b = labelForSort(right, sortKey);
    let result;
    if (typeof a === "number" && typeof b === "number") result = descending ? b - a : a - b;
    else result = collator.compare(String(a), String(b));
    if (result === 0) result = collator.compare(String(left.name || left.id), String(right.name || right.id));
    return result;
  });
}

function validFieldsForView(view, schemas) {
  return new Set((schemas?.[view]?.facets || []).map(({ field }) => field));
}

export function parseState(search, schemas) {
  const params = new URLSearchParams(String(search || "").replace(/^\?/, ""));
  const view = params.get("view") === "diseases" ? "diseases" : "models";
  const validFields = schemas ? validFieldsForView(view, schemas) : null;
  const filters = new Map();
  for (const [key, value] of params.entries()) {
    if (!key.startsWith("f.")) continue;
    const field = key.slice(2);
    if (!field || (validFields && !validFields.has(field)) || !value) continue;
    if (!filters.has(field)) filters.set(field, new Set());
    filters.get(field).add(value);
  }
  return {
    view,
    query: params.get("q") || "",
    sort: params.get("sort") || "name",
    filters,
  };
}

export function serializeState(state) {
  const params = new URLSearchParams();
  if (state.view === "diseases") params.set("view", "diseases");
  if (state.query) params.set("q", state.query);
  if (state.sort && state.sort !== "name") params.set("sort", state.sort);
  const filters = state.filters instanceof Map ? Object.fromEntries(state.filters) : state.filters || {};
  for (const field of Object.keys(filters).sort()) {
    const values = filters[field] instanceof Set ? [...filters[field]] : filters[field] || [];
    [...new Set(values)].sort(collator.compare).forEach((value) => params.append(`f.${field}`, value));
  }
  const encoded = params.toString();
  return encoded ? `?${encoded}` : "";
}

export function displayFacetValue(value, definition) {
  return value === MISSING_VALUE ? definition?.missingLabel || "Not reported" : value;
}

export function planNeighborhoodLayout(neighborhood, { maxPerSide = 4 } = {}) {
  const nodes = Array.isArray(neighborhood?.nodes) ? neighborhood.nodes : [];
  const edges = Array.isArray(neighborhood?.edges) ? neighborhood.edges : [];
  const nodeById = new Map(nodes.map((node) => [node.id, node]));
  const focusId = neighborhood?.focus_node_id;
  const modelId = neighborhood?.model_node_id;
  const causalEdges = edges.filter(
    (edge) =>
      edge?.kind === "causal" &&
      nodeById.has(edge.source_id) &&
      nodeById.has(edge.target_id) &&
      (edge.source_id === focusId || edge.target_id === focusId),
  );
  const compareNodes = (leftId, rightId) => {
    const left = nodeById.get(leftId)?.label || leftId;
    const right = nodeById.get(rightId)?.label || rightId;
    return collator.compare(String(left), String(right));
  };
  const upstream = [
    ...new Set(
      causalEdges
        .filter((edge) => edge.target_id === focusId && edge.source_id !== focusId)
        .map((edge) => edge.source_id),
    ),
  ].sort(compareNodes);
  const downstream = [
    ...new Set(
      causalEdges
        .filter((edge) => edge.source_id === focusId && edge.target_id !== focusId)
        .map((edge) => edge.target_id),
    ),
  ].sort(compareNodes);
  const limit = Number.isFinite(maxPerSide)
    ? Math.max(0, Math.floor(maxPerSide))
    : Number.POSITIVE_INFINITY;
  const visibleUpstream = upstream.slice(0, limit);
  const visibleDownstream = downstream.slice(0, limit);
  const width = 1000;
  const nodeWidth = 240;
  const nodeHeight = 70;
  const laneGap = 88;
  const laneCount = Math.max(visibleUpstream.length, visibleDownstream.length, 1);
  const height = Math.max(320, laneCount * laneGap + 48);
  const focusY = (height - nodeHeight) / 2;
  const placements = [];

  const place = (id, role, x, y) => {
    if (!nodeById.has(id)) return;
    placements.push({
      placementId: `${role}:${id}`,
      id,
      role,
      x,
      y,
      width: nodeWidth,
      height: nodeHeight,
    });
  };
  const laneTop = (count) =>
    count ? (height - (nodeHeight + (count - 1) * laneGap)) / 2 : focusY;

  place(modelId, "model", 500, Math.max(10, focusY - 112));
  visibleUpstream.forEach((id, index) =>
    place(id, "upstream", 150, laneTop(visibleUpstream.length) + index * laneGap),
  );
  place(focusId, "focus", 500, focusY);
  visibleDownstream.forEach((id, index) =>
    place(id, "downstream", 850, laneTop(visibleDownstream.length) + index * laneGap),
  );

  const placementByRoleAndId = new Map(
    placements.map((placement) => [`${placement.role}:${placement.id}`, placement]),
  );
  const focusPlacement = placementByRoleAndId.get(`focus:${focusId}`);
  const modelPlacement = placementByRoleAndId.get(`model:${modelId}`);
  const edgePlans = [];
  for (const edge of edges) {
    let sourcePlacement;
    let targetPlacement;
    let loop = false;
    if (edge.kind === "model_mechanism") {
      sourcePlacement = modelPlacement;
      targetPlacement = focusPlacement;
    } else if (edge.kind === "causal" && edge.source_id === focusId && edge.target_id === focusId) {
      sourcePlacement = focusPlacement;
      targetPlacement = focusPlacement;
      loop = true;
    } else if (edge.kind === "causal" && edge.target_id === focusId) {
      sourcePlacement = placementByRoleAndId.get(`upstream:${edge.source_id}`);
      targetPlacement = focusPlacement;
    } else if (edge.kind === "causal" && edge.source_id === focusId) {
      sourcePlacement = focusPlacement;
      targetPlacement = placementByRoleAndId.get(`downstream:${edge.target_id}`);
    }
    if (!sourcePlacement || !targetPlacement) continue;
    edgePlans.push({
      edge,
      sourcePlacement,
      targetPlacement,
      loop,
    });
  }

  return {
    width,
    height,
    nodeWidth,
    nodeHeight,
    placements,
    edges: edgePlans,
    hiddenUpstream: upstream.length - visibleUpstream.length,
    hiddenDownstream: downstream.length - visibleDownstream.length,
    upstreamCount: upstream.length,
    downstreamCount: downstream.length,
  };
}
