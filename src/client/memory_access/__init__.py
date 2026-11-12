"""Controlled access to contemporaneous private client memories."""
PAIR_VERSIONS = ('Q3inner_memory', 'inner_memory_no')
# One version, two branches: a shared prefix is generated once, then continued with past inner
# records provided or withheld (see client/Q3compare_memory/README.md).
COMPARE_VERSION = 'Q3compare_memory'
COMPARE_MODES = ('provided', 'withheld')
# Versions sharing the trait router and the private_position inner stage.
# Same fork design; the inner record also holds the client's stance toward the counselor's latest utterance.
COMPARE_STANCE_VERSION = 'Q3compare_memory_private_position'
LEGACY_CM_VERSIONS = ('cm', 'cm_split', 'cm_temporal', 'cm_state', 'cm_tools', 'cm_review', 'cm_full')
CM_VERSIONS = tuple('Q3'+v for v in LEGACY_CM_VERSIONS)
ALL_CM_VERSIONS = (*CM_VERSIONS, *LEGACY_CM_VERSIONS)
COMPARE_VERSIONS = (COMPARE_VERSION, COMPARE_STANCE_VERSION, *ALL_CM_VERSIONS)
INNER_VERSIONS = (*PAIR_VERSIONS, *COMPARE_VERSIONS)
VERSIONS = ('Q3public', 'Q3oracle', 'Q3full', 'Q3select', 'Q3ragtool', *INNER_VERSIONS)
