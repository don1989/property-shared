# Skill Observations Log

---

### Observation 1
**Date:** 2026-05-17
**Session context:** Investigating persistent EPC bug — property_epc tool returning wrong data when house number unknown (Rightmove use case)
**Skill:** mcp-primitive-classification / fastmcp-design-review
**Type:** Design pattern — modes vs separate tools
**Issue:** A single tool (`property_epc`) with an optional `address` parameter was used to encode three fundamentally different behaviours: address-matched single cert, area aggregate summary, and (missing) postcode browse. The "mode" encoding hid the output schema instability from the LLM — different input shapes produce incompatible output shapes, which causes repeated failed fix attempts because each fix addresses one mode without accounting for the others.
**Suggested improvement:** When a tool's output schema changes significantly based on an optional parameter, that is a signal to split into separate tools. The classification rule — "LLM decides which to invoke" — works best when each tool has a stable, predictable output contract.
**Principle:** One tool, one output schema. Modes that produce incompatible output shapes belong in separate tools.
**Status:** OPEN

---

### Observation 2
**Date:** 2026-05-17
**Session context:** Same session — EPC fix investigation
**Skill:** General debugging / investigation methodology
**Type:** Investigation order — verify data availability before designing around its absence
**Issue:** Multiple rounds of fix attempts were made to the address matching and EPC tool design without first checking whether the Rightmove PAGE_MODEL `address` dict contains UPRN or a full structured address. The scraper extracts only three keys (`displayAddress`, `outcode`, `incode`) from a dict that may contain more. If UPRN is present, the entire fuzzy matching problem is bypassed via direct EPC UPRN lookup.
**Suggested improvement:** Before designing a workaround for missing data, verify the data is actually missing at the source. A single live dump of `address_info.keys()` from a real Rightmove listing detail page would have resolved the architectural question before any code was written.
**Principle:** Check the raw payload before designing around assumed data gaps. The fix may already be in the data.
**Status:** OPEN

---

### Observation 3
**Date:** 2026-05-17
**Session context:** Same session — EPC fuzzy matcher scoring analysis
**Skill:** Systematic debugging
**Type:** Bug — fuzzy matcher produces wrong-street false positives
**Issue:** `match_score` in `address_matching.py` scores "CAVENDISH CRESCENT NORTH" at 36 against "Cavendish Crescent South" — above the 30-point threshold — because `extract_street` takes only the first two words after stripping a leading number, so both map to "cavendish crescent". Word-overlap scoring adds more points on shared words. The result: the matcher returns an EPC cert from the wrong street as a confident match. Additionally, `extract_number` naively matches the flat number in "FLAT 1, 5 HIGH STREET" rather than the building number, causing all flat-format EPC addresses to score ~9 when the target has no house number.
**Suggested improvement:** `extract_street` should include directional/qualifier words (North, South, East, West, Upper, Lower) as part of the street token rather than truncating at 2 words. `extract_number` should skip "FLAT N," and "APARTMENT N," prefixes before extracting the building number. The 30-point threshold should be raised when no house number is present in the target (confidence is inherently lower).
**Principle:** Address matching edge cases must be tested with a score matrix before shipping — the failure modes are not obvious from reading the code.
**Status:** CLOSED — fixed in commit 9db9425. extract_number strips flat/apartment prefixes, extract_street takes 3 words, match_epc_address raises threshold to 50 when target has no house number. Score matrix verified: wrong-street dropped from 36→6, flat cert with building number rose from 12→62.

---

### Observation 4
**Date:** 2026-05-17
**Session context:** Same session — structured_content vs content in FastMCP ToolResult
**Skill:** fastmcp-design-review / mcp-primitive-classification
**Type:** Clarification — structured_content is a client-side channel, not guaranteed LLM context
**Issue:** There was uncertainty about whether `structured_content` in FastMCP's `ToolResult` is visible to the LLM. Investigation confirmed: `structured_content` maps to `structuredContent` in `CallToolResult` in the MCP wire protocol — it is returned alongside `content` but whether it is injected into the LLM's context window is a host implementation decision. For Prefab dashboard rendering in property_app, structured_content drives the UI components, not the LLM. For tasks where the LLM must read and reason on data, the data must be in `content` (text blocks).
**Suggested improvement:** When designing tools where the LLM needs to browse or reason on returned data, use plain dict returns (serialized to JSON text in `content`). Reserve `ToolResult` with `structured_content` for: (a) image embedding, (b) Prefab/dashboard rendering, (c) programmatic downstream consumers. Document this distinction in the MCP tool design guidelines.
**Principle:** If the LLM needs to read it, put it in `content`. `structured_content` is for machines.
**Status:** OPEN

---

### Observation 5
**Date:** 2026-05-17
**Session context:** Rightmove PAGE_MODEL dump to check for UPRN — live diagnostic script against real listing 88378815
**Skill:** systematic-debugging / add-data-source
**Type:** Investigation result — data availability determines architecture
**Issue:** Multiple EPC fix attempts assumed UPRN was unavailable from Rightmove without verifying. Live dump confirmed: no UPRN. But the dump also revealed `sizings` — a structured array with numeric floor area per unit (sqm, sqft) — which the scraper was throwing away entirely. `floor_area_sqm` from `sizings` is a strong EPC discriminator requiring no address matching. The field was already being fetched; we just weren't capturing it.
**Suggested improvement:** When investigating a missing-data bug, dump the full raw payload before designing a workaround. A diagnostic script (`scripts/rightmove_address_dump.py`) that prints all keys should be the first step. Also worth scanning top-level keys for promising fields (`epcGraphs`, `sizings`, `entranceFloor`, `buildingId`) — several were present and uncaptured.
**Principle:** Dump the raw payload first. The fix is often in data you're already receiving but not capturing.
**Status:** OPEN

---

### Observation 6
**Date:** 2026-05-17
**Session context:** Adding `_extract_sizings` helper to `models/rightmove.py`
**Skill:** General — code style / helper extraction heuristic
**Type:** Feedback — when to write a helper vs inline a field extraction
**Issue:** Initial instinct was to inline `floor_area_sqm`/`floor_area_sqft` extraction directly into `from_page_model`. User corrected: extraction that iterates a list, branches on unit type, guards against missing values, and returns a tuple warrants a dedicated helper — consistent with `_extract_images`, `_extract_floorplans`, `_extract_display_size`, `_extract_key_features`.
**Suggested improvement:** Apply this heuristic: if it's a single `.get()` with a type coercion, inline it (`_safe_int`/`_safe_float`/`_str_or_none` already handle this). If it iterates a structure, branches on values, or returns multiple things, write a named helper. The distinction is complexity, not line count.
**Principle:** Write a helper when extraction iterates or branches. Inline it when it's a single `.get()` with coercion — that's what the `_safe_*` helpers are already for.
**Status:** OPEN

---

### Observation 7
**Date:** 2026-05-17
**Session context:** Design review of `property_epc_search` — user questioned whether `lmk_key` in the slim response was deliberate or noise
**Skill:** fastmcp-design-review / mcp-primitive-classification
**Type:** Design pattern — every key returned by a tool must have a load-bearing consumer tool
**Issue:** `lmk_key` was included in the `property_epc_search` slim response, but the described follow-up workflow used `property_epc(postcode, address)` — not `get_certificate(lmk_key)`. So `lmk_key` was present with nowhere to go: it looked deliberate but was functionally noise. `EPCClient.get_certificate()` already existed in the client but hadn't been exposed as an MCP tool.
**Suggested improvement:** Before including a lookup key in a tool's response, verify there is a corresponding tool that accepts it. If `lmk_key` is in the response, `get_certificate(lmk_key)` must exist as a tool. If the follow-up tool uses `address` instead, drop `lmk_key`. The fix is either expose the tool or remove the key — not leave both in an inconsistent state.
**Principle:** Every key returned by a browse/list tool that is intended as a follow-up identifier must have a corresponding lookup tool that accepts it. Keys without consumers are noise.
**Status:** OPEN

---

### Observation 8
**Date:** 2026-05-17
**Session context:** Tightening `property_epc_search` docstring — cross-reference instruction was suggestive, not imperative
**Skill:** fastmcp-design-review
**Type:** Tool description language — imperative vs suggestive for required steps
**Issue:** The original docstring said "Match by floor_area proximity (within ~5 sqm) and property_type" — phrased as a suggestion. LLMs treat suggestive phrasing as optional guidance they can override with their own reasoning. For a required cross-referencing step (the whole point of the tool), this is too weak: the LLM may skip or loosen the constraint depending on context.
**Suggested improvement:** Use "You MUST" for steps that are non-negotiable. The updated docstring reads "You MUST cross-reference each cert's floor_area against the listing's floor_area_sqm (accept within ±5 sqm) AND property_type must match." Also explicitly handle the fallback: "If floor_area is unavailable on the listing, filter by property_type only and return all candidates." Covering the fallback prevents the LLM from guessing when the discriminator is missing.
**Principle:** Required cross-referencing steps in tool descriptions must use imperative language ("MUST", "must match"). Suggestive phrasing ("match by", "use X to") is treated as optional. Always cover the fallback case explicitly.
**Status:** OPEN
