"""Candidate frame scoring: pixel-statistics quality gate, OCR text novelty, and a diversity-aware
selector. Similarity uses a normalised low-resolution thumbnail vector (no model download, CPU
only); it removes near-duplicate slides but is not semantic, so final relevance is always decided
by the vision model that sees the images.
"""
