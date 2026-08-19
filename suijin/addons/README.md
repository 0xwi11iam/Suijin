# Addons — the zero-boilerplate rung

Drop a folder with a `main.py` here and every public function in it
becomes an agent tool at boot. No manifest, no entry file, no JSON.

```
suijin/addons/my_tools/main.py
```

```python
def greet(name: str = "world") -> str:
    """Say hello to someone."""
    return f"Hello, {name}!"
```

Rules:
- public callables only (leading `_` = skipped), defined in that module
- first docstring line = tool description; parameter names = tool args
- return a string (that's what the agent sees); validate your inputs
- folders starting with `_` are dormant (`_example/` below)
- need a skill.md, permissions, or lifecycle? `suijin module adopt
  my_tools` graduates the addon into a full pack under suijin/modules/
