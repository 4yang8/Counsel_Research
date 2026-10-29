"""Free-form client note; no predefined psychological categories."""
EMPTY = '(내용 없음)'
# Empty mappings keep read-only APIs compatible; no state definitions are sent.
STATES = {}
INNER = {}

def parse_note(text):
    return {'states': {}, 'inner': {}}
