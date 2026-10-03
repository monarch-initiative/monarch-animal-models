import assert from "node:assert/strict";
import test from "node:test";

import {
  MISSING_VALUE,
  applyFilters,
  facetCounts,
  matchesSearch,
  parseState,
  serializeState,
  sortRecords,
} from "../src/site/assets/core.js";

const FACETS = [
  { field: "source_label", type: "string" },
  { field: "species_label", type: "string", missingLabel: "Not reported / not normalized" },
  { field: "human_gene_symbols", type: "array", operator: "or" },
];

const RECORDS = [
  { id: "a", name: "Mecp2 null mouse", source_label: "DisMech", species_label: "Mus musculus",
    human_gene_symbols: ["MECP2"], counterpart_count: 2, disease_labels: ["Rett Syndrome"] },
  { id: "b", name: "Mecp2<tm1.1Bird>/Y", source_label: "MGI", species_label: "Mus musculus",
    human_gene_symbols: ["MECP2"], counterpart_count: 1, disease_labels: ["Rett syndrome"] },
  { id: "c", name: "scn1lab mutant", source_label: "ZFIN", species_label: "Danio rerio",
    human_gene_symbols: ["SCN1A"], counterpart_count: 0, disease_labels: ["Dravet syndrome"] },
  { id: "d", name: "Canine model", source_label: "DisMech", species_label: null,
    human_gene_symbols: [], counterpart_count: 0, disease_labels: ["Ataxia"] },
];

test("missing species stays visible as its own facet value", () => {
  const counts = facetCounts(RECORDS, new Map(), FACETS, "species_label");
  assert.equal(counts.get(MISSING_VALUE), 1);
  assert.equal(counts.get("Mus musculus"), 2);
});

test("filters combine across facets and OR within one", () => {
  const filters = new Map([
    ["species_label", new Set(["Mus musculus"])],
    ["source_label", new Set(["DisMech", "MGI"])],
  ]);
  assert.deepEqual(applyFilters(RECORDS, filters, FACETS).map((r) => r.id), ["a", "b"]);
});

test("search matches gene symbols case-insensitively", () => {
  assert.ok(matchesSearch(RECORDS[2], "scn1a", ["human_gene_symbols"]));
  assert.ok(!matchesSearch(RECORDS[0], "scn1a", ["human_gene_symbols"]));
});

test("counterpart sort is descending with a name tie-break", () => {
  assert.deepEqual(sortRecords(RECORDS, "counterparts").map((r) => r.id), ["a", "b", "d", "c"]);
});

test("disease sort uses the first disease label", () => {
  assert.deepEqual(sortRecords(RECORDS, "disease").map((r) => r.id), ["d", "c", "a", "b"]);
});

test("state round-trips through the URL for the diseases view", () => {
  const schemas = { models: { facets: FACETS }, diseases: { facets: [{ field: "coverage_label" }] } };
  const state = parseState("?view=diseases&sort=kg_models&f.coverage_label=KG+models+only&f.bogus=x", schemas);
  assert.equal(state.view, "diseases");
  assert.equal(state.sort, "kg_models");
  assert.deepEqual([...state.filters.keys()], ["coverage_label"]);
  assert.equal(serializeState(state), "?view=diseases&sort=kg_models&f.coverage_label=KG+models+only");
});
