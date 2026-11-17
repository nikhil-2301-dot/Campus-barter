# How to add an admin action

Example: an admin does something to a listing, a report or a user.

1. **Permission.** Add an action name to `can()` in `app/permissions.py` that starts from `can("view_admin", None, user)`
   (it already refuses guests, students and suspended admins) and adds the state rule (for example "the report is open",
   "the target is not me"). Return a real `True`/`False`.
2. **Rule test table.** In `tests/test_permission_matrix.py` add the action to `ACTIONS` with the resource states to try, and
   extend `expected()` with the same rule written out in plain words. The test that lists every action in `permissions.py` fails until you do.
3. **Service.** In `app/admin_service.py` (or `report_service.py`) write the function. More than one row changes -> wrap in `with Tx(conn):`
   (`from .tx import NOW, Tx`). Make it safe to repeat (doing it twice changes nothing the second time). Raise `AdminProblem(message, status)` for a refused state.
4. **Route.** In `app/admin.py` with `@admin_required`; look up the row through a service function, check `can(...)` again with the row, then call the service.
   Destructive actions get a GET confirm page and a `confirm_open` + `dialog` button (see `listing_remove`); one-click actions use `post_button`.
5. **Template** under `app/templates/admin/`, from `components/` macros.
6. **Guards:** a row in `ROUTES` (tests/test_permission_matrix.py) and the admin route table in tests/test_admin.py, a row in `page_plan` for a GET page, a ceiling in `CEILINGS`.
7. Tests: allowed for admin, 403 for students, redirect for guests, refused state, repeat is harmless, the effect on the data.
8. `bash scripts/check.sh`; update CHANGELOG.md.
