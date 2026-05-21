# Context overlay

LOUD reads `.md` files in this directory and prepends them to the system
prompt at every chat turn. Use this to give LOUD specialized knowledge
about your stack (architecture, conventions, runbooks, etc.).

You can also drop user-specific context in `~/.loud/context/` — those files
override the bundled ones with the same name.
