"""Evaluation harness.

Not a service: unlike the sub-packages listed in ADR-0003, this is a tool, so it
may import from the services it measures. Running the search functions directly
rather than over HTTP keeps a network hop out of a number that is supposed to
isolate retrieval quality.
"""
