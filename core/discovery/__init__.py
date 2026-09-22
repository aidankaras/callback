"""Finding postings at their source.

This is the only package in `core` that reaches the network. It reads the
public posting APIs employers publish through their applicant-tracking
systems -- the canonical copy of a posting that job boards repost -- and
nothing else; see `core.discovery.fetch` for the rules every request obeys.
"""
