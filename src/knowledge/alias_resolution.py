import logging
from typing import Dict, Any, List
from collections import defaultdict

from src.knowledge.graph import KnowledgeGraph

logger = logging.getLogger(__name__)


def verify_and_apply_alias_table(
    alias_table: Dict[str, str],
    graph: KnowledgeGraph,
    batch_size: int = 500,
) -> Dict[str, int]:
    """
    Verify alias resolution mapping from Phase 2 and ensure Neo4j entity nodes
    have their `aliases` property fully populated with all surface forms.

    1. Groups surface forms by canonical target.
    2. Flags any ambiguous mappings or UNKNOWN_ALIAS items for manual review.
    3. Merges surface forms into the canonical entity node's `aliases` property in Neo4j.
    4. Returns verification statistics.
    """
    if not alias_table:
        return {"resolved": 0, "flagged": 0, "canonical_count": 0}

    canonical_to_aliases: Dict[str, List[str]] = defaultdict(list)
    flagged_count = 0
    resolved_count = 0

    for surface_form, canonical in alias_table.items():
        clean_surface = surface_form.strip()
        clean_canonical = canonical.strip()

        if not clean_surface or not clean_canonical:
            continue

        if clean_canonical.upper() == "UNKNOWN_ALIAS" or clean_surface.upper() == "UNKNOWN_ALIAS":
            logger.warning("Flagged ambiguous alias pair for manual review: '%s' -> '%s'", clean_surface, clean_canonical)
            flagged_count += 1
            continue

        canonical_to_aliases[clean_canonical].append(clean_surface)
        resolved_count += 1

    # Batch update Neo4j entities with their alias arrays
    updates = []
    for canonical_name, aliases in canonical_to_aliases.items():
        # Ensure canonical name is also included in aliases set
        all_variants = sorted(list(set(aliases + [canonical_name])))
        updates.append({
            "canonical_name": canonical_name,
            "new_aliases": all_variants,
        })

    cypher_update = """
    UNWIND $batch AS item
    MATCH (e:Entity)
    WHERE toLower(e.name) = toLower(item.canonical_name)
       OR e.id = item.canonical_name
    SET e.aliases = reduce(acc = [], x IN coalesce(e.aliases, []) + item.new_aliases |
        CASE WHEN x IN acc THEN acc ELSE acc + x END
    )
    """

    for i in range(0, len(updates), batch_size):
        batch = updates[i : i + batch_size]
        graph.neo4j.execute_write(cypher_update, {"batch": batch})

    logger.info(
        "Alias table verification complete: %d variants resolved across %d canonical entities (%d flagged).",
        resolved_count, len(canonical_to_aliases), flagged_count,
    )

    return {
        "resolved": resolved_count,
        "flagged": flagged_count,
        "canonical_count": len(canonical_to_aliases),
    }
