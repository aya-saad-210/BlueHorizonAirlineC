# notifications_logic.py
# This file holds the small piece of "who is authenticated right now" state that
# drives the notifications concern.
#
# The scenario: assign_reserve_crew and issue_compensation are NOT registered on
# the server when it starts. A front-desk ops agent only sees the read-only
# tools and rebook_passenger. Those two tools only appear once the connected
# client calls authenticate_supervisor with valid credentials -- at that point
# the server registers them AND pushes a real tools/list_changed notification,
# instead of making the client poll or guess.
#
# NOTE: this is a simplified, single-connection demo (stdio, one client at a
# time), so a plain module-level dict is enough to track "is this session
# authenticated". A production, multi-client version would key this by session
# ID instead of using one shared flag.

# Hardcoded demo credentials -- fine for a class project demo, never do this
# with real passwords in a real system.
KNOWN_SUPERVISORS = {
    "sup_001": "1234",
    "sup_002": "5678",
}

session_state = {
    "supervisor_authenticated": False,
    "supervisor_id": None,
}


def check_supervisor_credentials(supervisor_id: str, pin: str) -> bool:
    """
    Validates a supervisor_id/pin pair against the known demo supervisors.
    """
    expected_pin = KNOWN_SUPERVISORS.get(supervisor_id)
    return expected_pin is not None and expected_pin == pin