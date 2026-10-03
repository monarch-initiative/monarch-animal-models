import {
  applyFilters,
  displayFacetValue,
  facetCounts,
  matchesSearch,
  normalizeText,
  planNeighborhoodLayout,
  parseState,
  serializeState,
  sortRecords,
} from "./core.js";

const catalog = window.ANIMAL_MODELS_INDEX;
if (!catalog?.models || !catalog?.schemas) {
  throw new Error("The generated animal model catalog could not be loaded.");
}

const $ = (selector) => document.querySelector(selector);
const refs = {
  search: $("#catalog-search"),
  searchLabel: $("#search-label"),
  clearSearch: $("#clear-search"),
  description: $("#view-description"),
  facets: $("#facet-list"),
  filters: $("#filters-panel"),
  clearAll: $("#clear-all"),
  activeFilters: $("#active-filters"),
  resultsHeading: $("#results-heading"),
  count: $("#result-count"),
  resultList: $("#result-list"),
  empty: $("#empty-state"),
  emptyClear: $("#empty-clear"),
  sort: $("#sort-select"),
  loadMore: $("#load-more"),
  mobileCount: $("#mobile-filter-count"),
  filterToggle: $("#filter-toggle"),
  closeFilters: $("#close-filters"),
  backdrop: $("#filter-backdrop"),
  dialog: $("#record-dialog"),
  dialogContent: $("#dialog-content"),
  dialogKicker: $("#dialog-kicker"),
  dialogClose: $("#dialog-close"),
};

const VIEW_LABELS = {
  models: { heading: "Animal models", search: "Search animal models", noun: "model", kicker: "Animal model record", hash: "model" },
};
const modelById = new Map(catalog.models.map((record) => [record.id, record]));
const dismechEntryById = new Map(Object.entries(catalog.dismech_entries || {}));
const counterpartById = new Map((catalog.counterparts || []).map((pair) => [pair.id, pair]));

let state = { ...parseState(location.search, catalog.schemas), view: "models" };
let visibleLimit = 24;
const expandedFacets = new Set();
const pageSize = 24;

// --- full records, loaded on demand from data/details/<shard>.js ---

const detailCache = new Map();
const shardLoads = new Map();

window.ANIMAL_MODELS_DETAIL = (shard, records) => {
  for (const [id, record] of Object.entries(records)) detailCache.set(id, record);
  shardLoads.get(shard)?.resolve();
};

function loadShard(shard) {
  if (!shardLoads.has(shard)) {
    const pending = {};
    pending.promise = new Promise((resolve, reject) => {
      pending.resolve = resolve;
      pending.reject = reject;
    });
    shardLoads.set(shard, pending);
    const script = document.createElement("script");
    script.src = `./data/details/${encodeURIComponent(shard)}.js`;
    script.addEventListener("error", () => {
      shardLoads.delete(shard);
      pending.reject(new Error(`Could not load record details (${shard}).`));
    });
    document.head.append(script);
  }
  return shardLoads.get(shard).promise;
}

async function fullRecord(record) {
  if (!detailCache.has(record.id) && record.detail_shard) await loadShard(record.detail_shard);
  return detailCache.get(record.id) || record;
}

// --- utilities ---

function currentSchema() {
  return catalog.schemas[state.view];
}

function currentRecords() {
  return catalog[state.view];
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text != null) node.textContent = String(text);
  return node;
}

function externalLink(text, url, className) {
  if (!url) return null;
  let resolved;
  try {
    resolved = new URL(url, location.href);
  } catch {
    return null;
  }
  if (!["http:", "https:"].includes(resolved.protocol)) return null;
  const anchor = element("a", className, text);
  anchor.href = resolved.href;
  anchor.target = "_blank";
  anchor.rel = "noopener noreferrer";
  return anchor;
}

function curieLink(curie) {
  const value = String(curie || "");
  if (value.startsWith("PMID:")) return externalLink(value, `https://pubmed.ncbi.nlm.nih.gov/${value.slice(5)}/`);
  if (value.startsWith("MONDO:")) return externalLink(value, `https://monarchinitiative.org/${value}`);
  return null;
}

function formatNumber(value) {
  return new Intl.NumberFormat().format(Number(value) || 0);
}

function truncate(text, max = 235) {
  const clean = String(text || "").trim();
  return clean.length > max ? `${clean.slice(0, max - 1).trimEnd()}…` : clean;
}

function values(value) {
  if (value == null || value === "") return [];
  return Array.isArray(value) ? value.filter((item) => item != null && item !== "") : [value];
}

function filterCount() {
  let total = state.query ? 1 : 0;
  for (const selected of state.filters.values()) total += selected.size;
  return total;
}

function selectedValues(field) {
  return state.filters.get(field) || new Set();
}

function updateUrl({ push = false } = {}) {
  const next = `${location.pathname}${serializeState(state)}${location.hash}`;
  history[push ? "pushState" : "replaceState"](null, "", next);
}

function setFilter(field, value, checked) {
  const selected = new Set(selectedValues(field));
  if (checked) selected.add(value);
  else selected.delete(value);
  if (selected.size) state.filters.set(field, selected);
  else state.filters.delete(field);
  visibleLimit = pageSize;
  updateUrl();
  render();
}

function clearEverything() {
  state.query = "";
  state.filters.clear();
  refs.search.value = "";
  visibleLimit = pageSize;
  updateUrl();
  render();
}

function searchedRecords() {
  const schema = currentSchema();
  return currentRecords().filter((record) => matchesSearch(record, state.query, schema.searchableFields));
}

function filteredRecords() {
  const schema = currentSchema();
  return sortRecords(applyFilters(searchedRecords(), state.filters, schema.facets), state.sort);
}

// --- header, facets, filters ---

function renderHeader() {
  const stats = catalog.stats || {};
  $("#model-stat").textContent = formatNumber(stats.model_count);
  $("#species-stat").textContent = formatNumber(stats.species_count);
  $("#disease-stat").textContent = formatNumber(stats.modeled_disease_count);

  const schema = currentSchema();
  const labels = VIEW_LABELS[state.view];
  refs.searchLabel.textContent = labels.search;
  refs.search.placeholder = schema.searchPlaceholder;
  refs.search.value = state.query;
  refs.clearSearch.hidden = !state.query;
  refs.description.textContent = schema.description;
  refs.resultsHeading.textContent = labels.heading;
  refs.clearAll.disabled = filterCount() === 0;
  refs.mobileCount.textContent = filterCount() ? `(${filterCount()})` : "";

  refs.sort.replaceChildren();
  for (const option of schema.sortOptions || []) {
    const node = element("option", "", option.label);
    node.value = option.value;
    node.selected = option.value === state.sort;
    refs.sort.append(node);
  }
  if (![...refs.sort.options].some((option) => option.value === state.sort)) {
    state.sort = refs.sort.options[0]?.value || "name";
  }
}

// One shared collator: localeCompare with options builds a new one per call,
// which dominated render time on facets with thousands of values.
const facetCollator = new Intl.Collator(undefined, { sensitivity: "base", numeric: true });

function sortFacetEntries(entries, definition) {
  return entries.sort((left, right) => {
    if (definition.sortBy !== "alphabetical" && left[1] !== right[1]) return right[1] - left[1];
    return facetCollator.compare(left[0], right[0]);
  });
}

// Facets with more values than this get a type-to-find box instead of "Show more".
const FACET_SEARCH_THRESHOLD = 30;
const FACET_SEARCH_LIMIT = 15;
const FACET_TOP = 8;

const diseaseGroups = catalog.disease_groups || {};
const groupText = (id) => `${diseaseGroups[id] || id} (incl. subtypes)`;

function facetOption(definition, value, count, idSuffix, text = null) {
  const label = element("label", "facet-option");
  const checkbox = document.createElement("input");
  checkbox.type = "checkbox";
  checkbox.checked = selectedValues(definition.field).has(value);
  checkbox.id = `facet-${definition.field}-${idSuffix}`.replace(/[^a-zA-Z0-9_-]/g, "-");
  checkbox.addEventListener("change", () => setFilter(definition.field, value, checkbox.checked));
  label.append(
    checkbox,
    element("span", "facet-value", text ?? displayFacetValue(value, definition)),
    element("span", "facet-count", formatNumber(count)),
  );
  return label;
}

const GROUP_SUGGESTIONS = 5;

// Groups worth offering: they match the typed words and add models beyond the
// disease of the same name, i.e. they actually pull in subtypes.
function groupMatches(tokens, groups, entries) {
  if (!groups) return [];
  const exact = new Map(entries.map(([value, count]) => [String(value).toLocaleLowerCase(), count]));
  return [...groups.counts.entries()]
    .filter(([id, count]) => {
      const label = diseaseGroups[id] || id;
      const words = normalizeText(label).split(" ");
      return tokens.every((token) => words.some((word) => word.startsWith(token)))
        && count > (exact.get(label.toLocaleLowerCase()) || 0);
    })
    .sort((left, right) => right[1] - left[1])
    .slice(0, GROUP_SUGGESTIONS);
}

function facetFinder(definition, entries, groups = null) {
  const finder = element("div", "facet-finder");
  const input = document.createElement("input");
  input.type = "search";
  input.className = "facet-search";
  input.autocomplete = "off";
  input.spellcheck = false;
  input.placeholder = `Find among ${formatNumber(entries.length)}…`;
  input.setAttribute("aria-label", `Find ${definition.label.toLocaleLowerCase()} values`);
  const matches = element("div", "facet-options facet-matches");
  matches.setAttribute("aria-live", "polite");
  // Updates only the match list, so typing never loses focus to a full re-render.
  const update = () => {
    matches.replaceChildren();
    const tokens = normalizeText(input.value).split(/\s+/).filter(Boolean);
    if (!tokens.length) return;
    const groupHits = groupMatches(tokens, groups, entries);
    groupHits.forEach(([id, count], index) =>
      matches.append(facetOption(groups.definition, id, count, `group-${index}`, groupText(id))),
    );
    // Match each typed word against the start of a word in the value, so "rett"
    // finds "Rett syndrome" but not "Barrett esophagus".
    const found = entries.filter(([value]) => {
      const words = normalizeText(displayFacetValue(value, definition)).split(" ");
      return tokens.every((token) => words.some((word) => word.startsWith(token)));
    });
    found.slice(0, FACET_SEARCH_LIMIT).forEach(([value, count], index) =>
      matches.append(facetOption(definition, value, count, `match-${index}`)),
    );
    if (!found.length && !groupHits.length) matches.append(element("p", "facet-finder-note", "No matching values."));
    else if (found.length > FACET_SEARCH_LIMIT) {
      matches.append(
        element("p", "facet-finder-note", `${formatNumber(found.length - FACET_SEARCH_LIMIT)} more; keep typing to narrow.`),
      );
    }
  };
  input.addEventListener("input", update);
  finder.append(input, matches);
  return finder;
}

function renderFacets() {
  refs.facets.replaceChildren();
  const schema = currentSchema();
  const searchMatches = searchedRecords();

  for (const definition of schema.facets || []) {
    if (definition.hidden) continue;
    const counts = facetCounts(searchMatches, state.filters, schema.facets, definition.field);
    const chosen = selectedValues(definition.field);
    for (const value of chosen) {
      if (!counts.has(value)) counts.set(value, 0);
    }
    const entries = sortFacetEntries([...counts.entries()], definition);
    if (!entries.length) continue;

    const group = element("details", "facet-group");
    group.open = true;
    const summary = element("summary", "facet-summary");
    summary.append(element("span", "", definition.label));
    if (chosen.size) summary.append(element("span", "facet-selected", chosen.size));
    group.append(summary);

    const searchable = entries.length > FACET_SEARCH_THRESHOLD;
    const expanded = !searchable && expandedFacets.has(definition.field);
    // Always show selected values, then the most common ones.
    const visible = expanded
      ? entries
      : [
          ...entries.filter(([value]) => chosen.has(value)),
          ...entries.filter(([value]) => !chosen.has(value)).slice(0, Math.max(0, FACET_TOP - chosen.size)),
        ];
    const options = element("div", "facet-options");
    let groups = null;
    if (definition.groupField) {
      const groupDefinition = (schema.facets || []).find((item) => item.field === definition.groupField);
      groups = {
        definition: groupDefinition,
        counts: facetCounts(searchMatches, state.filters, schema.facets, definition.groupField),
      };
      for (const id of selectedValues(definition.groupField)) {
        options.append(facetOption(groupDefinition, id, groups.counts.get(id) || 0, `chosen-${id}`, groupText(id)));
      }
    }
    visible.forEach(([value, count], index) => options.append(facetOption(definition, value, count, index)));
    group.append(options);

    if (searchable) {
      group.append(facetFinder(definition, entries, groups));
    } else if (entries.length > FACET_TOP) {
      const more = element(
        "button",
        "facet-more",
        expanded ? "Show fewer" : `Show ${entries.length - FACET_TOP} more`,
      );
      more.type = "button";
      more.addEventListener("click", () => {
        if (expandedFacets.has(definition.field)) expandedFacets.delete(definition.field);
        else expandedFacets.add(definition.field);
        renderFacets();
      });
      group.append(more);
    }
    refs.facets.append(group);
  }
}

function removeFilter(field, value) {
  setFilter(field, value, false);
}

function renderActiveFilters() {
  refs.activeFilters.replaceChildren();
  const schema = currentSchema();
  if (state.query) {
    const chip = element("button", "filter-chip", `Search: “${truncate(state.query, 38)}” ×`);
    chip.type = "button";
    chip.setAttribute("aria-label", `Remove search ${state.query}`);
    chip.addEventListener("click", () => {
      state.query = "";
      refs.search.value = "";
      updateUrl();
      render();
    });
    refs.activeFilters.append(chip);
  }
  for (const definition of schema.facets || []) {
    for (const value of selectedValues(definition.field)) {
      const shown = definition.field === "related_ids"
        ? modelById.get(value)?.name || value
        : definition.field === "disease_group_ids"
          ? diseaseGroups[value] || value
          : displayFacetValue(value, definition);
      const text = `${definition.label}: ${shown} ×`;
      const chip = element("button", "filter-chip", text);
      chip.type = "button";
      chip.setAttribute("aria-label", `Remove filter ${text.slice(0, -2)}`);
      chip.addEventListener("click", () => removeFilter(definition.field, value));
      refs.activeFilters.append(chip);
    }
  }
}

function showRelated(record) {
  state.view = "models";
  state.query = "";
  state.filters = new Map([["related_ids", new Set([record.id])]]);
  visibleLimit = pageSize;
  if (refs.dialog.open) closeDialog();
  updateUrl({ push: true });
  render();
  document.getElementById("results")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function relatedButton(record) {
  const count = Number(record.counterpart_count) || 0;
  if (!count) return element("span", "coverage", "");
  const button = element("button", "related-link", `${count} related model${count === 1 ? "" : "s"}`);
  button.type = "button";
  button.addEventListener("click", () => showRelated(record));
  return button;
}

// --- cards ---

function tag(text, tone = "") {
  return element("span", `tag${tone ? ` tag-${tone}` : ""}`, text);
}

function addTags(container, items, limit = 4, tone = "") {
  const clean = values(items).filter(Boolean);
  clean.slice(0, limit).forEach((item) => container.append(tag(item, tone)));
  if (clean.length > limit) container.append(tag(`+${clean.length - limit}`));
}

function metadataItem(label, value) {
  const item = element("div", "metadata-item");
  item.append(element("span", "metadata-label", label), element("span", "metadata-value", value));
  return item;
}

function cardButton(record, view, label = "View details") {
  const button = element("button", "detail-button", label);
  button.type = "button";
  button.addEventListener("click", () => openRecord(record, view));
  return button;
}

function cardTitle(record, view) {
  const heading = element("h4", "card-title");
  const open = element("button", "title-button", record.name);
  open.type = "button";
  open.addEventListener("click", () => openRecord(record, view));
  heading.append(open);
  return heading;
}

function speciesText(record) {
  if (record.species_label) return record.species_label;
  return record.species_as_curated ? `${record.species_as_curated} (not normalized)` : "Species not reported";
}

function modelCard(record) {
  const card = element("article", "result-card model-card");
  const top = element("div", "card-topline");
  top.append(tag(speciesText(record), "model"));
  if (record.model_category_label) top.append(element("span", "source-kind", record.model_category_label));
  const context = element("p", "card-context", values(record.disease_labels).join(" · ") || "Disease not reported");
  const description = element(
    "p",
    "card-description",
    truncate(record.description || (record.strain_background ? `Background: ${record.strain_background}` : "No description recorded.")),
  );
  const metadata = element("div", "card-metadata");
  metadata.append(
    metadataItem("Human gene", values(record.human_gene_symbols).join(", ") || "Not reported"),
    metadataItem("Model gene", values(record.model_gene_symbols).join(", ") || "Not reported"),
  );
  const tags = element("div", "tag-list");
  if (values(record.relationships).length) addTags(tags, record.relationships, 3, "mechanism");
  else addTags(tags, record.allele_labels, 3);
  const footer = element("div", "card-footer");
  const left = element("span", "card-footer-meta");
  left.append(element("span", "card-source", record.source_label), relatedButton(record));
  footer.append(left, cardButton(record, "models"));
  card.append(top, cardTitle(record, "models"), context, description, metadata, tags, footer);
  return card;
}

function renderResults() {
  const records = filteredRecords();
  refs.resultList.replaceChildren();
  refs.resultList.setAttribute("aria-busy", "false");
  const noun = VIEW_LABELS[state.view].noun;
  const total = currentRecords().length;
  refs.count.textContent = `${formatNumber(records.length)} of ${formatNumber(total)} ${noun}${total === 1 ? "" : "s"}`;
  records.slice(0, visibleLimit).forEach((record) => {
    refs.resultList.append(modelCard(record));
  });
  refs.empty.hidden = records.length !== 0;
  refs.loadMore.hidden = records.length <= visibleLimit;
  if (records.length > visibleLimit) {
    refs.loadMore.textContent = `Show ${formatNumber(Math.min(pageSize, records.length - visibleLimit))} more of ${formatNumber(records.length)}`;
  }
}

function render() {
  renderHeader();
  renderActiveFilters();
  renderFacets();
  renderResults();
}

// --- detail building blocks ---

function definitionList(rows) {
  const list = element("dl", "detail-definitions");
  for (const [label, value] of rows) {
    const clean = values(value);
    if (!clean.length) continue;
    const wrapper = element("div", "definition-row");
    wrapper.append(element("dt", "", label));
    const detail = element("dd");
    clean.forEach((item, index) => {
      if (index) detail.append(document.createTextNode(", "));
      if (item instanceof Node) detail.append(item);
      else if (typeof item === "object") {
        const text = item.symbol || item.label || item.id;
        detail.append(externalLink(text, item.url) || document.createTextNode(text));
      } else detail.append(document.createTextNode(String(item)));
    });
    wrapper.append(detail);
    list.append(wrapper);
  }
  return list;
}

function detailSection(title, className = "") {
  const section = element("section", `detail-section ${className}`.trim());
  section.append(element("h3", "", title));
  return section;
}

function renderEvidence(evidence) {
  const section = detailSection("Evidence");
  const items = values(evidence);
  if (!items.length) {
    section.append(element("p", "missing-value", "No structured evidence block is recorded for this item."));
    return section;
  }
  const list = element("div", "evidence-list");
  items.forEach((item) => {
    const card = element("article", "evidence-card");
    const reference = item.reference_title || item.reference || "Evidence source";
    card.append(externalLink(reference, item.reference_url, "evidence-reference") || element("p", "evidence-reference", reference));
    if (item.snippet) card.append(element("blockquote", "", item.snippet));
    if (item.explanation) card.append(element("p", "", item.explanation));
    const labels = [item.supports, item.evidence_source].filter(Boolean).join(" · ");
    if (labels) card.append(element("p", "evidence-meta", labels));
    list.append(card);
  });
  section.append(list);
  return section;
}

function recordButton(record, view, extra) {
  const item = element("div", "related-record");
  if (extra) item.append(element("span", "related-basis", extra));
  const button = element("button", "related-title", record.name);
  button.type = "button";
  button.addEventListener("click", () => openRecord(record, view));
  item.append(button);
  return item;
}

const svgNamespace = "http://www.w3.org/2000/svg";
const htmlNamespace = "http://www.w3.org/1999/xhtml";
let mechanismGraphSequence = 0;

function svgElement(tagName, attributes = {}) {
  const node = document.createElementNS(svgNamespace, tagName);
  for (const [name, value] of Object.entries(attributes)) {
    if (value != null) node.setAttribute(name, String(value));
  }
  return node;
}

function graphNodeClass(node, role) {
  const classes = ["mechanism-graph-node", `mechanism-graph-node--${role}`];
  const knownKinds = new Set([
    "animal_model",
    "pathophysiology",
    "phenotype",
    "biochemical",
    "unresolved",
    "ambiguous",
  ]);
  classes.push(`mechanism-graph-node--${knownKinds.has(node.kind) ? node.kind : "other"}`);
  if (!node.resolved) classes.push("mechanism-graph-node--unresolved");
  return classes.join(" ");
}

function graphRoleLabel(role, node) {
  if (role === "model") return "Animal model";
  if (role === "focus") return node.resolved ? "Focal mechanism" : "Unresolved focal mechanism";
  if (role === "upstream") return "Upstream";
  if (role === "downstream") return "Downstream";
  return node.kind_label || "Pathograph node";
}

function graphEdgeStatement(edge, nodesById) {
  const source = nodesById.get(edge.source_id)?.label || "Unresolved source";
  const target = nodesById.get(edge.target_id)?.label || "Unresolved target";
  if (edge.kind === "model_mechanism") {
    return `${source} — ${edge.label || "model relationship"} — ${target}. This is a non-causal model-to-mechanism assertion.`;
  }
  const relationship = edge.label || "Causes";
  const details = [
    edge.causal_link_type_label || "Directness not reported",
    values(edge.intermediate_mechanisms).length
      ? `via ${edge.intermediate_mechanisms.join(", ")}`
      : null,
    values(edge.hypothesis_groups).length
      ? `hypothesis ${edge.hypothesis_groups.join(", ")}`
      : null,
  ].filter(Boolean);
  return `${source} ${relationship.toLocaleLowerCase()} ${target}${details.length ? ` (${details.join("; ")})` : ""}.`;
}

function graphEdgePath(plan) {
  const source = plan.sourcePlacement;
  const target = plan.targetPlacement;
  if (plan.loop) {
    const right = source.x + source.width / 2;
    const top = source.y;
    return `M ${right - 10} ${top + 10} C ${right + 90} ${top - 65}, ${right + 90} ${top + 105}, ${right - 6} ${top + 58}`;
  }
  if (plan.edge.kind === "model_mechanism") {
    const sourceX = source.x;
    const sourceY = source.y + source.height;
    const targetX = target.x;
    const targetY = target.y;
    const middle = (sourceY + targetY) / 2;
    return `M ${sourceX} ${sourceY} C ${sourceX} ${middle}, ${targetX} ${middle}, ${targetX} ${targetY}`;
  }
  const leftToRight = source.x <= target.x;
  const sourceX = source.x + (leftToRight ? source.width / 2 : -source.width / 2);
  const targetX = target.x + (leftToRight ? -target.width / 2 : target.width / 2);
  const sourceY = source.y + source.height / 2;
  const targetY = target.y + target.height / 2;
  const middle = (sourceX + targetX) / 2;
  return `M ${sourceX} ${sourceY} C ${middle} ${sourceY}, ${middle} ${targetY}, ${targetX} ${targetY}`;
}

function graphNodeEdgeSummary(nodeId, role, neighborhood, nodesById) {
  const incident = neighborhood.edges.filter((edge) => {
    if (role === "model") return edge.kind === "model_mechanism" && edge.source_id === nodeId;
    if (role === "upstream") return edge.kind === "causal" && edge.source_id === nodeId;
    if (role === "downstream") return edge.kind === "causal" && edge.target_id === nodeId;
    return edge.source_id === nodeId || edge.target_id === nodeId;
  });
  return incident.map((edge) => graphEdgeStatement(edge, nodesById));
}

function mechanismGraphInspector(placement, neighborhood, nodesById) {
  const node = nodesById.get(placement.id);
  const inspector = element("div", "mechanism-graph-inspector");
  inspector.setAttribute("aria-live", "polite");
  inspector.append(
    element("p", "mechanism-graph-inspector-role", graphRoleLabel(placement.role, node)),
    element("h6", "", node.label),
  );
  if (node.description) inspector.append(element("p", "mechanism-graph-inspector-description", node.description));

  const metadata = [
    node.kind_label,
    node.biological_scale_label,
    node.mechanism_confidence_label,
  ].filter(Boolean);
  if (metadata.length) inspector.append(element("p", "mechanism-graph-inspector-meta", metadata.join(" · ")));
  if (!node.resolved) {
    const status = node.resolution_status === "ambiguous" ? "Ambiguous source target" : "Unresolved source target";
    inspector.append(element("p", "mechanism-graph-unresolved-note", status));
    if (values(node.candidate_kinds).length) {
      inspector.append(
        element(
          "p",
          "mechanism-graph-inspector-meta",
          `Possible source types: ${node.candidate_kinds.join(", ")}.`,
        ),
      );
    }
  }

  const statements = graphNodeEdgeSummary(node.id, placement.role, neighborhood, nodesById);
  if (statements.length) {
    const list = element("ul", "mechanism-graph-inspector-edges");
    statements.forEach((statement) => list.append(element("li", "", statement)));
    inspector.append(list);
  }
  const link = externalLink("Open this node in DisMech", node.url, "inline-link");
  if (link) inspector.append(link);
  return inspector;
}

function closestGraphPlacement(current, placements, direction) {
  const currentX = current.x;
  const currentY = current.y + current.height / 2;
  const candidates = placements.filter((candidate) => {
    const candidateY = candidate.y + candidate.height / 2;
    if (direction === "ArrowLeft") return candidate.x < currentX;
    if (direction === "ArrowRight") return candidate.x > currentX;
    if (direction === "ArrowUp") return candidateY < currentY;
    return candidateY > currentY;
  });
  return candidates.sort((left, right) => {
    const leftY = left.y + left.height / 2;
    const rightY = right.y + right.height / 2;
    const horizontalDirection = direction === "ArrowLeft" || direction === "ArrowRight";
    const leftPrimary = horizontalDirection ? Math.abs(left.x - currentX) : Math.abs(leftY - currentY);
    const rightPrimary = horizontalDirection ? Math.abs(right.x - currentX) : Math.abs(rightY - currentY);
    const leftCross = horizontalDirection ? Math.abs(leftY - currentY) : Math.abs(left.x - currentX);
    const rightCross = horizontalDirection ? Math.abs(rightY - currentY) : Math.abs(right.x - currentX);
    return leftPrimary - rightPrimary || leftCross - rightCross;
  })[0];
}

function mechanismGraphDesktop(layout, neighborhood, nodesById, graphId, selection) {
  const wrapper = element("div", "mechanism-graph-desktop");
  const laneLabels = element("div", "mechanism-graph-lanes");
  laneLabels.append(
    element("span", "", `Upstream (${layout.upstreamCount})`),
    element("span", "", "Focal modeled event"),
    element("span", "", `Downstream (${layout.downstreamCount})`),
  );
  wrapper.append(laneLabels);
  const legend = element("div", "mechanism-graph-legend");
  const modelRelationship = neighborhood.edges.find((edge) => edge.kind === "model_mechanism");
  legend.append(
    element("span", "mechanism-graph-legend-causal", "Causal direction"),
    element(
      "span",
      "mechanism-graph-legend-model",
      `${modelRelationship?.label || "Model relationship"} (non-causal)`,
    ),
  );
  wrapper.append(legend);

  const stage = element("div", "mechanism-graph-stage");
  stage.setAttribute("role", "group");
  stage.setAttribute("aria-label", "Local causal neighborhood");
  stage.setAttribute("aria-describedby", `${graphId}-edges ${graphId}-instructions`);
  const svg = svgElement("svg", {
    class: "mechanism-graph-svg",
    viewBox: `0 0 ${layout.width} ${layout.height}`,
    preserveAspectRatio: "xMidYMid meet",
  });
  const definitions = svgElement("defs");
  const marker = svgElement("marker", {
    id: `${graphId}-arrow`,
    viewBox: "0 0 10 10",
    refX: "8.25",
    refY: "5",
    markerWidth: "7",
    markerHeight: "7",
    orient: "auto-start-reverse",
  });
  marker.append(svgElement("path", { d: "M 0 0 L 10 5 L 0 10 z", class: "mechanism-graph-arrow" }));
  definitions.append(marker);
  svg.append(definitions);

  const edgeLayer = svgElement("g", { class: "mechanism-graph-edges", "aria-hidden": "true" });
  const edgePaths = [];
  layout.edges.forEach((plan) => {
    const indirect = String(plan.edge.causal_link_type || "").startsWith("INDIRECT");
    const unknown = plan.edge.causal_link_type === "UNKNOWN";
    const path = svgElement("path", {
      d: graphEdgePath(plan),
      class: [
        "mechanism-graph-edge",
        plan.edge.kind === "model_mechanism"
          ? "mechanism-graph-edge--model"
          : "mechanism-graph-edge--causal",
        indirect ? "mechanism-graph-edge--indirect" : "",
        unknown ? "mechanism-graph-edge--unknown" : "",
      ].filter(Boolean).join(" "),
      "marker-end": plan.edge.kind === "causal" ? `url(#${graphId}-arrow)` : null,
      "vector-effect": "non-scaling-stroke",
    });
    edgePaths.push({ path, plan });
    edgeLayer.append(path);
  });
  svg.append(edgeLayer);

  const buttons = [];
  layout.placements.forEach((placement) => {
    const node = nodesById.get(placement.id);
    const foreignObject = svgElement("foreignObject", {
      x: placement.x - placement.width / 2,
      y: placement.y,
      width: placement.width,
      height: placement.height,
      class: "mechanism-graph-node-box",
    });
    const button = document.createElementNS(htmlNamespace, "button");
    button.type = "button";
    button.className = graphNodeClass(node, placement.role);
    button.dataset.placementId = placement.placementId;
    button.setAttribute("aria-pressed", String(placement.placementId === selection.id));
    button.setAttribute(
      "aria-label",
      `${graphRoleLabel(placement.role, node)}: ${node.label}. ${graphNodeEdgeSummary(node.id, placement.role, neighborhood, nodesById).join(" ")}`,
    );
    button.tabIndex = placement.placementId === selection.id ? 0 : -1;
    button.append(
      element("span", "mechanism-graph-node-role", graphRoleLabel(placement.role, node)),
      element("span", "mechanism-graph-node-label", node.label),
    );
    button.addEventListener("click", () => selection.select(placement, { focus: false }));
    button.addEventListener("keydown", (event) => {
      if (event.key === "Home") {
        event.preventDefault();
        const focus = layout.placements.find(({ role }) => role === "focus");
        if (focus) selection.select(focus, { focus: true });
        return;
      }
      if (!["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) return;
      const target = closestGraphPlacement(placement, layout.placements, event.key);
      if (!target) return;
      event.preventDefault();
      selection.select(target, { focus: true });
    });
    buttons.push({ button, placement });
    foreignObject.append(button);
    svg.append(foreignObject);
  });
  stage.append(svg);
  wrapper.append(stage);
  selection.desktop = { buttons, edgePaths };
  return wrapper;
}

function mechanismGraphMobileGroup(title, placements, total, nodesById, neighborhood, selection) {
  const section = element("section", "mechanism-graph-mobile-group");
  const count = placements.length === total
    ? `${total}`
    : `${placements.length} of ${total} shown`;
  section.append(element("h6", "", `${title} (${count})`));
  if (!placements.length) {
    section.append(element("p", "missing-value", "None recorded."));
    return section;
  }
  const list = element("div", "mechanism-graph-mobile-list");
  placements.forEach((placement) => {
    const node = nodesById.get(placement.id);
    const button = element("button", "mechanism-graph-mobile-node");
    button.type = "button";
    button.dataset.placementId = placement.placementId;
    button.setAttribute("aria-pressed", String(placement.placementId === selection.id));
    button.append(
      element("span", "mechanism-graph-node-role", graphRoleLabel(placement.role, node)),
      element("span", "mechanism-graph-node-label", node.label),
    );
    button.setAttribute(
      "aria-label",
      `${graphRoleLabel(placement.role, node)}: ${node.label}. ${graphNodeEdgeSummary(node.id, placement.role, neighborhood, nodesById).join(" ")}`,
    );
    button.addEventListener("click", () => selection.select(placement, { focus: false }));
    selection.mobileButtons.push({ button, placement });
    list.append(button);
  });
  section.append(list);
  return section;
}

function mechanismGraphMobile(layout, neighborhood, nodesById, selection) {
  const wrapper = element("div", "mechanism-graph-mobile");
  const byRole = (role) => layout.placements.filter((placement) => placement.role === role);
  const relationship = neighborhood.edges.find((edge) => edge.kind === "model_mechanism");
  wrapper.append(
    element(
      "p",
      "mechanism-graph-mobile-note",
      `${relationship?.label || "Model relationship"} is a non-causal model-to-mechanism assertion. Upstream and downstream sections preserve authored causal direction.`,
    ),
  );
  wrapper.append(
    mechanismGraphMobileGroup("Animal model relationship", byRole("model"), 1, nodesById, neighborhood, selection),
    mechanismGraphMobileGroup("Upstream", byRole("upstream"), layout.upstreamCount, nodesById, neighborhood, selection),
    mechanismGraphMobileGroup("Focal modeled event", byRole("focus"), 1, nodesById, neighborhood, selection),
    mechanismGraphMobileGroup("Downstream", byRole("downstream"), layout.downstreamCount, nodesById, neighborhood, selection),
  );
  return wrapper;
}

function mechanismNeighborhood(mechanism, mechanismIndex) {
  const neighborhood = mechanism.neighborhood;
  if (!neighborhood?.nodes?.length || !neighborhood?.edges?.length) return null;
  const graphId = `mechanism-neighborhood-${++mechanismGraphSequence}`;
  const nodesById = new Map(neighborhood.nodes.map((node) => [node.id, node]));
  const details = element("details", "mechanism-neighborhood");
  details.open = mechanismIndex === 0;
  const initialLayout = planNeighborhoodLayout(neighborhood, { maxPerSide: 4 });
  const summary = element("summary", "mechanism-neighborhood-summary");
  summary.append(
    element("span", "mechanism-neighborhood-summary-title", "Local causal neighborhood"),
    element(
      "span",
      "mechanism-neighborhood-summary-count",
      `${initialLayout.upstreamCount} upstream · ${initialLayout.downstreamCount} downstream`,
    ),
  );
  details.append(summary);

  const instructions = element(
    "p",
    "visually-hidden",
    "Use arrow keys to move between graph nodes, Home to return to the focal mechanism, and Enter or Space to select.",
  );
  instructions.id = `${graphId}-instructions`;
  const edgeList = element("ul", "visually-hidden");
  edgeList.id = `${graphId}-edges`;
  neighborhood.edges.forEach((edge) => edgeList.append(element("li", "", graphEdgeStatement(edge, nodesById))));
  details.append(instructions, edgeList);

  const body = element("div", "mechanism-neighborhood-body");
  let expanded = false;
  let selectedPlacementId = `focus:${neighborhood.focus_node_id}`;

  function renderBody() {
    const layout = planNeighborhoodLayout(neighborhood, {
      maxPerSide: expanded ? Number.POSITIVE_INFINITY : 4,
    });
    if (!layout.placements.some(({ placementId }) => placementId === selectedPlacementId)) {
      selectedPlacementId = `focus:${neighborhood.focus_node_id}`;
    }
    body.replaceChildren();
    const graphViews = element("div", "mechanism-neighborhood-views");
    const inspectorHost = element("div", "mechanism-graph-inspector-host");
    const selection = {
      id: selectedPlacementId,
      desktop: null,
      mobileButtons: [],
      select(placement, { focus = false } = {}) {
        selectedPlacementId = placement.placementId;
        selection.id = selectedPlacementId;
        for (const { button, placement: item } of selection.desktop?.buttons || []) {
          const active = item.placementId === selectedPlacementId;
          button.setAttribute("aria-pressed", String(active));
          button.tabIndex = active ? 0 : -1;
          if (active && focus) button.focus();
        }
        for (const { button, placement: item } of selection.mobileButtons) {
          button.setAttribute("aria-pressed", String(item.placementId === selectedPlacementId));
        }
        for (const { path, plan } of selection.desktop?.edgePaths || []) {
          const selectedNode = placement.id;
          path.classList.toggle(
            "is-selected",
            plan.edge.source_id === selectedNode || plan.edge.target_id === selectedNode,
          );
        }
        inspectorHost.replaceChildren(mechanismGraphInspector(placement, neighborhood, nodesById));
      },
    };
    graphViews.append(
      mechanismGraphDesktop(layout, neighborhood, nodesById, graphId, selection),
      mechanismGraphMobile(layout, neighborhood, nodesById, selection),
    );
    const selected = layout.placements.find(({ placementId }) => placementId === selectedPlacementId)
      || layout.placements.find(({ role }) => role === "focus");
    if (selected) selection.select(selected);
    body.append(graphViews, inspectorHost);

    const footer = element("div", "mechanism-graph-footer");
    if (initialLayout.hiddenUpstream || initialLayout.hiddenDownstream) {
      const toggle = element(
        "button",
        "mechanism-graph-show-all",
        expanded
          ? "Show fewer direct neighbors"
          : `Show all direct neighbors (${initialLayout.hiddenUpstream + initialLayout.hiddenDownstream} more)`,
      );
      toggle.type = "button";
      toggle.setAttribute("aria-expanded", String(expanded));
      toggle.addEventListener("click", () => {
        expanded = !expanded;
        renderBody();
        body.querySelector(".mechanism-graph-show-all")?.focus();
      });
      footer.append(toggle);
    }
    const sourceLink = externalLink(
      "Open full pathograph in DisMech",
      neighborhood.source_pathograph_url,
      "mechanism-graph-source-link",
    );
    if (sourceLink) footer.append(sourceLink);
    body.append(footer);
  }

  renderBody();
  details.append(body);
  return details;
}


const BASIS_TEXT = {
  CURATED: "curated",
  SAFE_MAPPING: "safe mapping",
  DERIVED: "derived from KG orthology",
  NOT_REPORTED: "not reported",
  NOT_NORMALIZED: "not normalized",
};

function geneCell(gene, basis) {
  if (!gene) return document.createTextNode("Not reported");
  const span = element("span");
  const text = gene.symbol || gene.id;
  span.append(externalLink(text, gene.url) || document.createTextNode(text));
  if (gene.id && gene.id !== text) span.append(document.createTextNode(` (${gene.id})`));
  span.append(element("span", "basis-note", ` · ${BASIS_TEXT[basis] || basis}`));
  return span;
}

function geneticSection(record) {
  const section = detailSection("Genetic composition");
  const components = values(record.genetic_components);
  section.append(
    element(
      "p",
      "association-explainer",
      "The human disease gene, the gene altered in the model organism, and the correspondence between them are recorded separately. Values derived from KG orthology are candidates, not source assertions.",
    ),
  );
  if (!components.length) {
    section.append(element("p", "missing-value", "No genes or alleles are recorded for this model."));
    return section;
  }
  const list = element("div", "evidence-list");
  components.forEach((component) => {
    const card = element("article", "evidence-card");
    const correspondence = component.correspondence;
    const support = values(correspondence?.support).map((item) =>
      [item.source, values(item.evidence).join(", ")].filter(Boolean).join(": "),
    );
    card.append(
      definitionList([
        ["Model organism gene", [geneCell(component.model_gene, component.model_gene_basis)]],
        ["Alleles", values(component.alleles).map((allele) => allele.label || allele.id)],
        [
          "Reagents",
          values(component.reagents).map((reagent) =>
            `${reagent.label || reagent.id} (${String(reagent.reagent_type || "").toLocaleLowerCase()})`),
        ],
        ["Human gene", [geneCell(component.human_gene, component.human_gene_basis)]],
        [
          "Correspondence",
          correspondence ? `${correspondence.relation.toLocaleLowerCase()} (${BASIS_TEXT[correspondence.basis] || correspondence.basis})` : null,
        ],
        ["Orthology support", support],
        ["Gene link from", component.source],
      ]),
    );
    list.append(card);
  });
  section.append(list);
  return section;
}

function diseaseAssociationSection(record) {
  const section = detailSection("Disease associations");
  const associations = values(record.disease_associations);
  if (!associations.length) {
    section.append(element("p", "missing-value", record.context_kind === "Module"
      ? "Module-level model: DisMech modules have no disease term."
      : "No MONDO disease is recorded for this entry."));
    return section;
  }
  const list = element("div", "evidence-list");
  associations.forEach((association) => {
    const card = element("article", "evidence-card");
    const disease = association.disease || {};
    const entries = values(association.dismech_entries).map((id) => {
      const entry = dismechEntryById.get(id);
      return entry ? externalLink(entry.name, entry.page_url) || entry.name : id;
    });
    card.append(
      definitionList([
        ["Disease", [curieLink(disease.id) ? (() => {
          const span = element("span", "", `${disease.label} `);
          span.append(curieLink(disease.id));
          return span;
        })() : disease.label || disease.id]],
        ["Originally asserted as", association.original_disease?.id],
        ["Asserted by", association.source],
        ["Relative to DisMech", association.dismech_precision ? association.dismech_precision.replaceAll("_", " ").toLocaleLowerCase() : null],
        [association.dismech_precision === "EXACT" ? "DisMech entry" : "Nearest DisMech entries", entries],
        ["Publications", values(association.publications).map((pmid) => curieLink(pmid) || pmid)],
        ["Evidence codes", association.evidence_codes],
      ]),
    );
    list.append(card);
  });
  section.append(list);
  return section;
}

function counterpartSection(record) {
  const section = detailSection("Related models");
  section.append(
    element(
      "p",
      "association-explainer",
      "Records from another source that share a publication, or the same species, human gene, and a closely related disease. Related is not the same as identical: one record may describe several experimental conditions that another source records separately.",
    ),
  );
  const pairs = values(record.counterpart_ids).map((id) => counterpartById.get(id)).filter(Boolean);
  if (!pairs.length) {
    section.append(element("p", "missing-value", "No related model in another source."));
    return section;
  }
  const showAll = element("button", "related-link", "Show these in the list");
  showAll.type = "button";
  showAll.addEventListener("click", () => showRelated(record));
  section.append(showAll);
  const list = element("div", "related-records");
  pairs.forEach((pair) => {
    const otherId = record.source === "DISMECH" ? pair.kg_model_id : pair.dismech_model_id;
    const other = modelById.get(otherId);
    if (!other) return;
    const item = recordButton(other, "models", other.source_label);
    item.append(
      element("span", "", [values(pair.basis_labels).join(" · "), pair.disease_relation_label].join(" — ")),
    );
    if (values(pair.shared_publications).length) {
      const pubs = element("span", "");
      pair.shared_publications.forEach((pmid, index) => {
        if (index) pubs.append(document.createTextNode(", "));
        pubs.append(curieLink(pmid) || document.createTextNode(pmid));
      });
      item.append(pubs);
    }
    list.append(item);
  });
  section.append(list);
  return section;
}

function mechanismSection(record) {
  const section = detailSection("Modeled mechanisms");
  const mechanisms = values(record.modeled_mechanisms);
  if (!mechanisms.length) {
    section.append(
      element(
        "p",
        "missing-value",
        record.source === "DISMECH"
          ? "No modeled mechanism links recorded."
          : "KG records carry disease associations, not DisMech pathograph links.",
      ),
    );
    return section;
  }
  mechanisms.forEach((mechanism, mechanismIndex) => {
    const card = element("article", "mechanism-card");
    const heading = element("h4");
    heading.append(externalLink(mechanism.target, mechanism.target_url) || document.createTextNode(mechanism.target));
    card.append(heading);
    const neighborhood = mechanismNeighborhood(mechanism, mechanismIndex);
    if (neighborhood) card.append(neighborhood);
    card.append(
      definitionList([
        ["Relationship", mechanism.relationship_label || "Not specified"],
        ["Fidelity", mechanism.fidelity_label || "Not specified"],
        ["Model scale", mechanism.model_scale],
      ]),
    );
    if (mechanism.description) card.append(element("p", "", mechanism.description));
    if (mechanism.limitations) card.append(element("p", "limitation", `Limitations: ${mechanism.limitations}`));
    section.append(card);
  });
  return section;
}

function sourceDatabaseUrl(curie) {
  const [prefix, local] = String(curie || "").split(/:(.*)/s);
  if (prefix === "ZFIN") return `https://zfin.org/${local}`;
  if (prefix === "MGI") return `https://www.informatics.jax.org/allele/genoview/MGI:${local}`;
  if (prefix === "RGD") return `https://rgd.mcw.edu/rgdweb/report/strain/main.html?id=${local}`;
  if (prefix === "MONDO") return `https://monarchinitiative.org/${curie}`;
  return null;
}

function identifierLine(curie, sourceLabel) {
  const line = element("p", "detail-identifier");
  line.append(element("span", "detail-identifier-id", curie));
  const sourceLink = externalLink(sourceLabel ? `View in ${sourceLabel}` : "View source", sourceDatabaseUrl(curie));
  if (sourceLink) line.append(sourceLink);
  if (!curie.startsWith("MONDO:")) {
    const monarch = externalLink("View in Monarch", `https://monarchinitiative.org/${curie}`);
    if (monarch) line.append(monarch);
  }
  return line;
}

function modelDetail(record) {
  const fragment = document.createDocumentFragment();
  const header = element("header", "detail-header");
  const title = element("h2", "", record.name);
  title.id = "dialog-title";
  header.append(title);
  if (record.genotype?.id) header.append(identifierLine(record.genotype.id, record.source_label));
  header.append(element("p", "detail-context", values(record.disease_labels).join(" · ") || record.context_name || ""));
  if (record.description) header.append(element("p", "detail-lede", record.description));
  const pills = element("div", "tag-list");
  addTags(pills, [record.source_label], 1, record.source === "DISMECH" ? "model" : "dataset");
  addTags(pills, [record.species_label, record.model_category_label], 2);
  header.append(pills);
  fragment.append(header);

  const profile = detailSection("Model profile");
  profile.append(
    definitionList([
      ["Source", `${record.source_label} (${record.source_group})`],
      ["Species", record.species ? [record.species] : null],
      ["Species as recorded", record.species_as_curated],
      ["Species basis", BASIS_TEXT[record.species_basis]],
      ["Model category", record.model_category_label],
      ["Category as recorded", record.model_category_as_curated],
      ["Genotype", record.genotype ? [record.genotype.label || record.genotype.id] : null],
      ["Genetic background", record.strain_background],
      ["NAMO class", record.namo_type],
      ["Publications", values(record.publications).map((pmid) => curieLink(pmid) || pmid)],
      ["Notes", record.notes],
    ]),
  );
  fragment.append(
    profile,
    geneticSection(record),
    diseaseAssociationSection(record),
    counterpartSection(record),
    mechanismSection(record),
  );
  if (values(record.associated_phenotypes).length) {
    const phenotypes = detailSection("Associated phenotypes");
    phenotypes.append(definitionList([["Phenotypes", record.associated_phenotypes]]));
    fragment.append(phenotypes);
  }
  if (record.source === "DISMECH") fragment.append(renderEvidence(record.evidence));

  const source = detailSection("Source and provenance", "source-section");
  source.append(
    element(
      "p",
      "",
      record.source === "DISMECH"
        ? `Transformed from DisMech ${record.context_kind === "Module" ? "module" : "disorder"} “${record.context_name}”.`
        : `Genotype ${record.id} and its model_of associations, from the Monarch KG (${record.source_label}).`,
    ),
  );
  const actions = element("div", "detail-actions");
  const pageLink = externalLink(record.source === "DISMECH" ? "Open rendered DisMech record" : "Open in Monarch", record.source_record_url, "primary-link");
  const yamlLink = externalLink("View source YAML", record.source_yaml_url, "secondary-link");
  if (pageLink) actions.append(pageLink);
  if (yamlLink) actions.append(yamlLink);
  source.append(actions);
  fragment.append(source);
  return fragment;
}

async function openRecord(record, view = "models") {
  const labels = VIEW_LABELS[view];
  refs.dialogKicker.textContent = labels.kicker;
  refs.dialogContent.replaceChildren(element("p", "missing-value", "Loading record…"));
  history.replaceState(null, "", `${location.pathname}${location.search}#${labels.hash}=${encodeURIComponent(record.id)}`);
  if (!refs.dialog.open) refs.dialog.showModal();
  try {
    const full = await fullRecord(record);
    refs.dialogContent.replaceChildren(modelDetail(full));
  } catch (error) {
    refs.dialogContent.replaceChildren(element("p", "missing-value", error.message));
  }
  refs.dialogContent.scrollTop = 0;
}

function closeDialog() {
  refs.dialog.close();
  history.replaceState(null, "", `${location.pathname}${location.search}`);
}

function openFromHash() {
  const match = location.hash.match(/^#(model)=(.+)$/);
  if (!match) return;
  let id;
  try {
    id = decodeURIComponent(match[2]);
  } catch {
    return;
  }
  const record = modelById.get(id);
  if (record) openRecord(record, "models");
}

function setFiltersOpen(open) {
  refs.filters.classList.toggle("is-open", open);
  refs.backdrop.hidden = !open;
  refs.filterToggle.setAttribute("aria-expanded", String(open));
  document.body.classList.toggle("filter-drawer-open", open);
  if (open) refs.closeFilters.focus();
  else refs.filterToggle.focus();
}

refs.search.addEventListener("input", () => {
  state.query = refs.search.value;
  visibleLimit = pageSize;
  updateUrl();
  render();
  refs.search.focus();
});
refs.clearSearch.addEventListener("click", () => {
  state.query = "";
  refs.search.value = "";
  updateUrl();
  render();
  refs.search.focus();
});
refs.sort.addEventListener("change", () => {
  state.sort = refs.sort.value;
  updateUrl();
  renderResults();
});
refs.clearAll.addEventListener("click", clearEverything);
refs.emptyClear.addEventListener("click", clearEverything);
refs.loadMore.addEventListener("click", () => {
  visibleLimit += pageSize;
  renderResults();
});
refs.filterToggle.addEventListener("click", () => setFiltersOpen(true));
refs.closeFilters.addEventListener("click", () => setFiltersOpen(false));
refs.backdrop.addEventListener("click", () => setFiltersOpen(false));
refs.dialogClose.addEventListener("click", closeDialog);
refs.dialog.addEventListener("click", (event) => {
  if (event.target === refs.dialog) closeDialog();
});
refs.dialog.addEventListener("cancel", (event) => {
  event.preventDefault();
  closeDialog();
});
window.addEventListener("popstate", () => {
  state = { ...parseState(location.search, catalog.schemas), view: "models" };
  render();
  openFromHash();
});

const provenance = catalog.provenance || {};
const dismechRevision = String(provenance.dismech?.revision || "unknown");
const namoRevision = String(provenance.namo?.revision || "unknown");
const generated = catalog.generated_at ? new Date(catalog.generated_at) : null;
$("#snapshot-summary").textContent = [
  generated && !Number.isNaN(generated.valueOf()) ? `Generated ${generated.toLocaleDateString()}` : "Generated snapshot",
  `DisMech ${dismechRevision === "unknown" ? "revision unknown" : dismechRevision.slice(0, 8)}`,
  `Monarch KG ${provenance.kg?.version || "version unknown"}`,
  `ZFIN ${provenance.zfin?.version || "version unknown"}`,
  `NAMO ${namoRevision === "unknown" ? "revision unknown" : namoRevision.slice(0, 8)}`,
  provenance.dismech?.dirty ? "DisMech source had local changes" : null,
  provenance.namo?.dirty ? "NAMO source had local changes" : null,
].filter(Boolean).join(" · ");

render();
openFromHash();
