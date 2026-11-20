"""Clean-room simulator of the published jury procedure.

It never imports the `auditenv` package: it talks to the environment only through
`judge_adapter.py` and re-derives every accounting check from the open files and the documented
posting rules. Organizer policies run in separate `python -I` processes that cannot import the
evaluator.
"""
