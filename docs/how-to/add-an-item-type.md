# How to add an item type (a category)

Item types are the list `CATEGORIES` in `app/constants.py`. The database does not constrain them, so this needs NO migration.

1. Add the new name to `CATEGORIES` in `app/constants.py` (the order is the order of the dropdown and of the landing tiles).
   Keep it short (it is shown as a stamp and in a filter), and not the name of an existing type.
2. Search for hard-coded lists: `grep -rn "Mini drafter" app tests` shows where examples live. Only code that counts the types by
   hand needs a change. Today these follow the constant by themselves: the listing form, the browse filter, `stats_service.landing_counts`.
3. Old rows with a removed type are not touched. If you REMOVE a type, existing listings keep the old value and still show;
   decide first whether to move them (that is a data migration: see add-a-listing-field.md for how to write one).
4. Tests: `tests/test_browse_ui.py` and `tests/test_listing_forms.py` compare the dropdown with `CATEGORIES`, so they pass by themselves.
   Add one test in `tests/test_listings.py` that creates a listing of the new type and finds it with `?category=<name>`.
5. Run `bash scripts/check.sh`. Update CHANGELOG.md (one line under "Unreleased").
