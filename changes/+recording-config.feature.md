Separate recording metadata, production configuration, and authored structure.
Video frontmatter now contains only `kind`, required `title`, and optional
`description`; recording-specific settings belong in one leading `config`
directive; and recordings use an optional `panes` directive followed by
repeated singular `beat` directives. The internal scene is derived from the
recording directory and title. Existing `scene` and list-valued `beats`
directives are no longer accepted.
