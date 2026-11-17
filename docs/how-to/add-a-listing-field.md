# How to add a field to a listing (with a migration)

Example: a text field `edition` (optional, up to 60 characters). Follow the order: schema, service, route, template, tests.

1. **Migration.** Create `migrations/0005_listing_edition.sql` (next free number; 4 digits, lower case, underscores):
   `ALTER TABLE listings ADD COLUMN edition TEXT CHECK (edition IS NULL OR length(edition) <= 60);`
   Never edit an old migration. Start the file with a one-line comment saying why. Apply with `flask --app app migrate`.
2. **Service.** In `app/listing_service.py` add the column to the INSERT in `create_listing` and the UPDATE in `update_listing`
   (parameters with `?`, same order as the tuple). `get_listing` and `browse` use `l.*`, so they already return it.
3. **Validation and limit.** In `app/listings.py` add `MAX_EDITION_LEN = 60`, a check in `_validate_listing_form`
   (return it in the data dict as `edition` and in `problems` when too long), and the limit in `_form_context()["limits"]`.
4. **Templates.** `app/templates/listings/form.html`: add `{{ field("edition", "Edition", value=..., problem=..., maxlength=limits.edition) }}`
   next to the other fields, and add `("edition", "Edition")` to `summary_fields` (the error summary). `listings/view.html`: add a row in the facts.
   Use only macros from `components/`; no inline style.
5. **Seed and tests.** If the demo data should show it, set it in `app/seed.py`. Add tests: saved on create, saved on edit,
   too long is refused with the message next to the field, shown on the detail page.
   No existing test pins the full column list of `listings`, so none should need editing; if one does, find out why before touching it.
6. **Privacy check.** A field that holds contact data must not appear in any page before a request is accepted
   (tests/test_exchange.py and tests/test_requests_ui.py guard this).
7. `bash scripts/check.sh`, then add a line to CHANGELOG.md.
