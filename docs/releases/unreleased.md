# Unreleased

Changes since v1.10.0.

## Numbering conversions

- `convert_numbering` can leave numbered table cells as they are (`select.include_tables: false`) and can set aside a
  file where any converted number would count differently (`counters: "keep"`) instead of flagging it.
- `convert_typed_numbers` with `match: "counters"` converts typed numbers whose values are the list's at that place,
  in another look than the template's: "1.01" into a "1.1" list, "PART 1 –" with an en dash into "PART 1 -". A typo, a
  skip or a duplicate still sets the file aside, and each number the list shows otherwise than typed is flagged.
