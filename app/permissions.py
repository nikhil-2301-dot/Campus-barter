"""Permission helper: can(action, resource, user).

Actions on listings:
  view   -- anyone logged in (or anonymous for public browse)
  create -- any non-suspended user
  edit   -- owner or admin (listing must not be removed)
  delete -- owner or admin (soft-delete to 'removed')
  restore -- admin only

Actions on exchange requests (resource = a request row that also carries the listing's
`owner_id`; the "owner" is the student who owns the requested listing):
  request_listing  -- not your own listing, listing available, you are not suspended, you are not an admin
                      (admins moderate; like requests themselves, swaps are between students)
  accept_request   -- owner, request pending
  decline_request  -- owner, request pending
  cancel_request   -- requester while pending or accepted; owner while accepted
  complete_request -- owner or requester, request accepted
  view_request     -- owner or requester only (admins are not parties to an exchange)
  view_contact     -- owner or requester, and only while accepted or completed

Actions for reports and moderation (C13):
  report_listing   -- logged in, not suspended, not an admin, not the owner, listing not removed
  view_admin       -- admin (not suspended)
  resolve_report   -- admin, and the report (resource) is still open (also used for dismiss)
  suspend_user     -- admin, target (resource) is a student: not yourself, not an admin, not already suspended
  unsuspend_user   -- admin, target is currently suspended

All checks return True/False; callers decide whether to 403 or 404.
"""

# Which side of a request may start each transition (the state rules are in can()).
REQUEST_ACTION_ROLES = {
    "accept": ("owner",),
    "decline": ("owner",),
    "cancel": ("requester", "owner"),
    "complete": ("owner", "requester"),
}


def request_role(req, user):
    """'owner', 'requester' or None: which side of the request `user` is on."""
    if user is None:
        return None
    if req["owner_id"] == user["id"]:
        return "owner"
    if req["requester_id"] == user["id"]:
        return "requester"
    return None


def can(action: str, resource, user) -> bool:
    """Return True if `user` (row dict or None) may perform `action` on `resource` (row dict)."""
    if action == "create_listing":
        return user is not None and not user["is_suspended"]

    if action in ("edit_listing", "delete_listing"):
        if user is None:
            return False
        if user["is_admin"]:
            return True
        return (
            resource["owner_id"] == user["id"]
            and resource["status"] != "removed"
        )

    if action == "restore_listing":
        return user is not None and bool(user["is_admin"])

    if action == "request_listing":
        return (user is not None and not user["is_suspended"] and not user["is_admin"]
                and resource["status"] == "available"
                and resource["owner_id"] != user["id"])

    if action.endswith("_request") and action[:-8] in REQUEST_ACTION_ROLES:
        verb = action[:-8]
        role = request_role(resource, user)
        if role is None or user["is_suspended"] or role not in REQUEST_ACTION_ROLES[verb]:
            return False
        status = resource["status"]
        if verb in ("accept", "decline"):
            return status == "pending"
        if verb == "cancel":
            return status == "accepted" or (status == "pending" and role == "requester")
        return status == "accepted"                     # complete

    if action == "view_request":
        return request_role(resource, user) is not None

    if action == "view_contact":
        return (request_role(resource, user) is not None
                and resource["status"] in ("accepted", "completed"))

    if action == "report_listing":
        return (user is not None and not user["is_suspended"] and not user["is_admin"]
                and resource["owner_id"] != user["id"] and resource["status"] != "removed")

    if action == "view_admin":
        return user is not None and bool(user["is_admin"]) and not user["is_suspended"]

    if action == "resolve_report":
        return can("view_admin", None, user) and resource["status"] == "open"

    if action == "suspend_user":
        return (can("view_admin", None, user) and resource["id"] != user["id"]
                and not resource["is_admin"] and not resource["is_suspended"])

    if action == "unsuspend_user":
        return can("view_admin", None, user) and bool(resource["is_suspended"])

    # Unknown action: deny
    return False
