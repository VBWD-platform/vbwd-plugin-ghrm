"""GHRM shared literals.

``VENDOR_ID_KEY`` is the marketplace attribution key, pinned to the documented
``vendor_id`` convention. GHRM itself does NOT stamp it: a GHRM package rides a
subscription plan, and subscription's checkout stamps ``vendor_id`` from the
plan's own ``vendor_id`` on purchase. This constant exists so the shared
convention is documented locally WITHOUT importing the marketplace plugin —
keeping the money path decoupled (DRY without inverting the dependency arrow).
"""
VENDOR_ID_KEY = "vendor_id"
