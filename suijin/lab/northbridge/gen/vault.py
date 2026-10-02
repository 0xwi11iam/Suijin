"""auto-generated runner: vault (factory-built)"""
from suijin.lab.northbridge.factory import run_service as _rs


def main():
    import sys as _s
    from suijin.lab.northbridge import catalog as _c
    _rs(next(s for s in _c.SERVICES if s["name"] == "vault"))


if __name__ == "__main__":
    main()
