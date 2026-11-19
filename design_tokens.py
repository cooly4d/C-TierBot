"""Design tokens for every Pillow card: colours named by what they are for, and the text sizes each card uses.

`image_utils` keeps the older `QUEUE_IMG_*` names as aliases of these, so existing code and tests still work.
`design-system/MASTER.md` documents the values; change them here, not in the renderers.
"""

# --- Surfaces ---
CANVAS = (18, 24, 37)            # every card's background
HEADER_TOP = (24, 33, 55)        # header band (flat headers use this alone)
HEADER_BOTTOM = (34, 48, 73)     # bottom of the gradient header band
PANEL = (22, 30, 46)             # team panels, the history and compare cards
PANEL_RAISED = (28, 37, 55)      # alternate table rows, inventory item cards
DIVIDER = (38, 50, 74)           # hairlines under headers and between rows
TIMELINE_FILL = (28, 40, 64)     # the queue card's MATCH TIMELINE box

# --- Text and meaning ---
TEXT = (235, 237, 240)
TEXT_MUTED = (168, 183, 207)     # secondary text, unknown results, footers
ACCENT = (255, 165, 50)          # averages, the highlighted game, Golden Fries amounts, MVP tag
WIN = (108, 199, 128)
LOSE = (214, 96, 96)

# Team panel header tints, paired with WIN / LOSE / TEXT_MUTED text.
WIN_BANNER = (27, 56, 47)
LOSE_BANNER = (56, 31, 41)
NEUTRAL_BANNER = (30, 41, 62)

# Timeline lanes: the faint line each team's row of game dots sits on.
TIMELINE_LANE = (52, 66, 94)

# /market offer cards.
SHOP_AVAILABLE_FILL = (40, 60, 40)
SHOP_BOUGHT_FILL = (60, 40, 40)

# Item rarity, the exact colours survev.de uses (RARITY_COLORS in client/src/stats/js/loadoutModal.ts).
# Stock and Common share a grey there too; the rarity name under each item tells them apart. Keys are the API's ints.
RARITY = {
    0: (197, 197, 197),   # Stock    #c5c5c5
    1: (197, 197, 197),   # Common   #c5c5c5
    2: (18, 255, 0),      # Uncommon #12ff00
    3: (0, 222, 255),     # Rare     #00deff
    4: (246, 0, 255),     # Epic     #f600ff
    5: (217, 97, 0),      # Mythic   #d96100
}
RARITY_NAMES = {0: "Stock", 1: "Common", 2: "Uncommon", 3: "Rare", 4: "Epic", 5: "Mythic"}

# --- Text sizes, in 1x pixels on the 1600px canvas (the queue and history cards draw them at 2x) ---
# Queue result and game cards
QUEUE_SCORE = 72
QUEUE_TITLE = 66
QUEUE_RESULT = 38
QUEUE_TEAM = 38
QUEUE_BODY = 35
QUEUE_LABEL = 30          # column headers, MATCH TIMELINE, IN PROGRESS, played/total labels
QUEUE_META = 30           # "Match Duration: 42m 18s"
QUEUE_FOOTER = 26         # unlinked / played-games legend
QUEUE_TIMELINE_LABEL = 26 # G1, G2 … under the lane dots
QUEUE_MVP = 24
# Compact header (inventory, shop, compare)
CARD_TITLE = 54
CARD_SUBTITLE = 28
CARD_AMOUNT_LABEL = 22
CARD_AMOUNT = 48
# Inventory
ITEM_NAME = 32
ITEM_RARITY = 24
ITEM_VALUE = 30
# Shop
OFFER_KIND = 24
OFFER_TITLE = 34
OFFER_PRICE = 34
OFFER_STATUS = 24
OFFER_CAPTION = 20
# Compare
COMPARE_NAME = 38
COMPARE_STATUS = 20
COMPARE_SECTION = 22
COMPARE_LABEL = 30
COMPARE_VALUE = 36
# Shared
EMPTY_STATE = 30
FOOTER = 22

# The one credit line, drawn at the bottom of the cards that show survev.de or NeatQueue data.
CREDIT_LINE = "Stats from survev.de and NeatQueue"
