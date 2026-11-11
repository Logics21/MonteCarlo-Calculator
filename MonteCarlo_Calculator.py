
"""
Function / Variable Tolerance Tool
-----------------------------------
Run with:
   python MonteCarlo_Calculator.py

Dependencies:
   pip install PyQt5 sympy matplotlib pint numpy SALib
"""

import sys
import re
import json
import keyword
import math
import os
import textwrap
import zipfile
from functools import lru_cache
from datetime import datetime

import numpy as np
import pint

from PyQt5.QtWidgets import (
   QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
   QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
   QComboBox, QHeaderView, QMessageBox, QSizePolicy, QFileDialog, QDialog,
   QTextEdit, QCheckBox, QScrollArea, QFrame, QSpinBox,
   QStyle, QStyleOptionHeader, QStyleOptionComboBox, QStyleOptionFrame
)
from PyQt5.QtGui import QDoubleValidator, QIntValidator, QFont, QColor
from PyQt5.QtCore import Qt, QRect, QEvent, QSize, QTimer

from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.mathtext import MathTextParser
from matplotlib.font_manager import FontProperties
from matplotlib.patches import Rectangle

import sympy
from sympy.printing.latex import LatexPrinter
from sympy.printing.numpy import NumPyPrinter
from sympy.parsing.sympy_parser import (
   parse_expr, standard_transformations,
   implicit_multiplication_application, convert_xor, split_symbols
)

from annotator import AnnotatorWidget, AnnotatorDialog
from help_text import HELP_HTML

APP_VERSION = "1.1.4"

# ----------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------

# Saved-project container: a zip holding inputs.json plus the annotator's
# PNGs under images/.  Keeps the JSON small and readable while storing
# image bytes without base64 bloat.
PROJECT_EXT = ".mcz"
PROJECT_FILTER = "MonteCarlo Project (*.mcz)"
PROJECT_JSON = "inputs.json"
PROJECT_IMAGE_DIR = "images/"

TABLE_HEADERS = [
   "Var", "Var Name", "Tol Type", "Upper Limit", "Lower Limit",
   "Nominal", "\u00b1 Tol", "Tol Band", "Unit", "Unit Preview",
   "Distribution", "Cp"
]
(COL_VAR, COL_NAME, COL_TOL, COL_UPPER, COL_LOWER,
COL_NOMINAL, COL_DEVIATION, COL_BAND, COL_UNIT,
COL_UNIT_PREVIEW, COL_DIST, COL_CP) = range(12)

# Process capability of the supplying process, per variable. Cp relates the
# tolerance width to the real spread: width = 6 * Cp * sigma. Cp = 1.00 means
# the limits sit at +/-3 sigma; Cp = 1.33 means the process is tighter than
# its tolerance and the true sigma is width / 7.98. Assuming 1.00 for every
# variable overstates the spread of any capable process.
DEFAULT_CP = 1.0
MIN_CP = 0.1
MAX_CP = 10.0
# Only these derive sigma from the limits; the others are bounded by them.
CP_AWARE_DISTRIBUTIONS = ("Normal", "Lognormal")

CP_TOOLTIP = (
   "Process capability of the supplying process.\n"
   "1.00 = limits are +/-3 sigma (the usual assumption).\n"
   "1.33 = process tighter than tolerance, sigma = width / 7.98.\n"
   "Applies to Normal and Lognormal only; Uniform and\n"
   "Triangular fill the limits by definition."
)

# Default target for the tolerance-allocation dialog. 1.33 is the common
# industry floor for a capable process.
DEFAULT_TARGET_CPK = 1.33

# Results dialog: preferred size, clamped to the available screen at open
# time. RESULTS_CHART_HEIGHT is the chart's minimum inside the scroll
# area, so the chart stays legible and the page scrolls instead.
RESULTS_DIALOG_WIDTH = 1250
RESULTS_DIALOG_HEIGHT = 900
RESULTS_CHART_HEIGHT = 520

# Convergence targets. CONV_SIGMA_PRECISION is how precisely the mean and
# standard deviation should be known, as a fraction of the output's own
# standard deviation -- 0.02 means "to within 2% of a sigma". 
# CONV_FAIL_REL_ERROR is the acceptable error on the failure rate relative
# to the rate itself, so 0.10 means "known to within +/-10% of its value",
# which needs about 100 failures to occur.
CONV_SIGMA_PRECISION = 0.02
CONV_FAIL_REL_ERROR = 0.10
# Only suggest cutting back when the current count is this many times more
# than needed, so the advice is worth acting on.
CONV_OVERKILL_FACTOR = 4.0
CONV_MIN_SUGGESTION = 500

# The Var columns start at VAR_COL_MIN_WIDTH and widen to fit the longest
# name in the table (see _fit_var_column), so names are never cut off.
VAR_COL_MIN_WIDTH = 90
# Room around a Var name inside its cell when sizing the column to fit.
VAR_COL_PADDING = 24

NARROW_COL_WIDTHS = {
   COL_VAR: VAR_COL_MIN_WIDTH,
   COL_TOL: 110,
   COL_UPPER: 100,
   COL_LOWER: 100,
   COL_NOMINAL: 100,
   COL_DEVIATION: 130,
   COL_BAND: 90,
   COL_DIST: 130,
   COL_CP: 60,
   COL_UNIT: 140,
}
# Fixed columns of the input table are fitted to their header and contents
# (see _fit_input_columns): as narrow as they can be, growing as needed up
# to the width above, which is the most each may take. Var has no limit,
# so a name is never cut off. Var Name and Unit Preview stretch to share
# whatever is left.
INPUT_FIT_COLS = [c for c in NARROW_COL_WIDTHS]
# Room around a header's text when fitting. Cells are measured by the
# widget style itself (see _cell_width), so no guessed padding is needed.
FIT_PADDING_HEADER = 18
WIDE_COLS = {COL_NAME, COL_UNIT_PREVIEW}

OUTPUT_HEADERS = ["Var", "Var Name", "Unit", "Unit Preview"]
(OUT_COL_VAR, OUT_COL_NAME, OUT_COL_UNIT, OUT_COL_UNIT_PREVIEW) = range(4)

OUT_NARROW_COL_WIDTHS = {OUT_COL_VAR: VAR_COL_MIN_WIDTH}
OUT_WIDE_COLS = {OUT_COL_NAME, OUT_COL_UNIT, OUT_COL_UNIT_PREVIEW}

# A "Fixed" variable has no tolerance at all: no limits, a single Nominal
# value, and therefore exactly one possible distribution. The Tol Type and
# the Distribution are locked together 1:1, so they deliberately share the
# same name -- there is no case where one is Fixed and the other isn't.
FIXED_TOL_TYPE = "Fixed"
FIXED_DISTRIBUTION = "Fixed"

VARIABLE_DISTRIBUTIONS = ["Normal", "Uniform", "Triangular", "Lognormal"]
DISTRIBUTIONS = VARIABLE_DISTRIBUTIONS + [FIXED_DISTRIBUTION]
TOL_TYPES = ["", "Symmetric", "Asymmetric", FIXED_TOL_TYPE]

METHOD_PEARSON = "Pearson Correlation"
METHOD_SRC = "Standardized Regression (SRC)"
METHOD_CONTRIBUTION = "Variance Contribution (%)"
METHOD_SOBOL = "Sobol Indices"
METHOD_OAT = "OAT Tornado (Deterministic)"
METHOD_ELASTICITY = "Elasticity (% / %)"

SENSITIVITY_METHODS = [
   METHOD_PEARSON,
   METHOD_SRC,
   METHOD_CONTRIBUTION,
   METHOD_SOBOL,
   METHOD_OAT,
   METHOD_ELASTICITY,
]

# Elasticity is derived symbolically, so there is no step size and no
# tolerance for deciding whether an exponent is constant -- the derived
# expression either contains variables or it doesn't. These points are
# only where the reported number's range is sampled, to say how far it can
# be trusted away from Nominal.
ELASTICITY_PROBE_POINTS = 5
# An output this small next to its own sweep range makes the percentage
# meaningless well before it actually reaches zero.
ELASTICITY_NEAR_ZERO_FRAC = 1e-3

ELASTICITY_REGIME_AFFINE = "affine"
ELASTICITY_REGIME_CONSTANT = "constant"
ELASTICITY_REGIME_LOCAL = "local"
ELASTICITY_REGIME_POINT = "point"
ELASTICITY_REGIME_NONE = "none"
# Correlations, SRC and Sobol indices are all bounded by +/-1, so a fixed
# axis keeps those charts comparable run to run. Methods whose values are
# unbounded pass xlim=None and get an axis fitted to the data.
SENSITIVITY_XLIM = (-1.15, 1.15)
BOOTSTRAP_METHODS = (METHOD_PEARSON, METHOD_SRC, METHOD_CONTRIBUTION)

SOBOL_BASE_SAMPLES = 512
# Sobol's samples come in linked groups (one base point plus one point
# per varying variable). A group with any undefined output is dropped
# whole; below this fraction of surviving groups the indices are no
# longer trustworthy and none are reported.
SOBOL_MIN_VALID_FRACTION = 0.5
N_BOOTSTRAP = 200
BOOTSTRAP_CI_PCT = 90
WORST_CASE_EXACT_LIMIT = 20

# How the worst-case range was arrived at. The linear route is exact
# without enumerating anything, so it must not claim to have checked every
# combination.
WORST_CASE_EXACT_LINEAR = "exact_linear"
WORST_CASE_EXACT_ENUMERATED = "exact_enumerated"
WORST_CASE_APPROXIMATE = "approximate"
WORST_CASE_MODE_LABELS = {
   WORST_CASE_EXACT_LINEAR: "exact, linear function",
   WORST_CASE_EXACT_ENUMERATED: "exact, all combinations",
   WORST_CASE_APPROXIMATE: "approximate, independent-swing",
}

# Corners evaluated per vectorized batch during exact worst-case
# enumeration. Caps peak memory at roughly (chunk x variables) floats no
# matter how many variables there are, instead of materializing all 2^n
# corners at once.
WORST_CASE_CHUNK = 1 << 16

# Limits are treated as +/-3 sigma throughout the RSS stack-up, so the
# reported RSS range uses the same multiplier.
RSS_SIGMA_MULTIPLIER = 3.0

# The subfunction list scrolls past this many rows instead of growing and
# squeezing the input variables table below it.
SUBFUNCTION_VISIBLE_ROWS = 3

# Height of the rendered-expression canvas. The equation fills the whole
# canvas (see LatexPreview), so this is the height of the equation itself
# rather than equation-plus-matplotlib-margins.
LATEX_CANVAS_MAX_HEIGHT = 84

# Main rendered expression. Shrinks from LATEX_FONT_SIZE toward
# LATEX_MIN_FONT_SIZE before it resorts to a horizontal scrollbar.
LATEX_FONT_SIZE = 18
LATEX_MIN_FONT_SIZE = 11

# Inline equation previews beside the Function and Subfunction fields.
# The width cap is generous because a long stack-up expression needs the
# room; the row's text field and the preview split the spare width. Font
# starts at MINI_LATEX_FONT_SIZE and steps down only as far as
# MINI_LATEX_MIN_FONT_SIZE, below which it would be unreadable anyway --
# the full-size render below the row is the place to read a long equation.
MINI_LATEX_HEIGHT = 40
MINI_LATEX_MIN_WIDTH = 220
MINI_LATEX_MAX_WIDTH = 620
MINI_LATEX_FONT_SIZE = 12
MINI_LATEX_MIN_FONT_SIZE = 8

# Character width of the monospace stats block. Sized to fit the 8.5in
# PDF page at the 9pt monospace font used by _export_pdf_report.
STATS_WRAP_WIDTH = 92
MAX_FUNCTION_SLOTS = 20

# Iterations, Seed and the two Eval limits belong to the function being
# analysed, not to the app: a different slot solves for a different output
# with its own acceptance limits, and a count that converges for one
# equation need not for another. They live in the slot alongside its text.
SLOT_SIM_FIELDS = ("iterations", "seed", "eval_min", "eval_max")
# Only Iterations has a useful starting value; the other three are
# optional and mean "not set" when blank.
DEFAULT_ITERATIONS = "10000"

# Reports round every number to at most this many decimal places, set in
# Settings. Trailing zeros are dropped, so fewer decimals stay as they are.
DEFAULT_REPORT_DECIMALS = 5
MAX_REPORT_DECIMALS = 15

# Process All summary table: column groups, each a list of
# (column label, relative PDF width). Shared by the results dialog and
# the PDF so both always show the same grouped columns.
EVAL_LIMITS_GROUP = "Eval Limits"
RSS_RANGE_GROUP = f"RSS \u00b1{RSS_SIGMA_MULTIPLIER:g}\u03c3"
PROCESS_ALL_COLUMN_GROUPS = [
   ("Function", [
       ("Slot", 0.6), ("Var", 0.8), ("Var Name", 2.2), ("Unit", 1.3),
   ]),
   ("Monte Carlo", [
       ("Iterations", 1.15), ("Seed", 0.9), ("Mean", 1.0),
       ("Min", 1.0), ("Max", 1.0),
   ]),
   ("Worst Case", [("Min", 1.0), ("Max", 1.0)]),
   (RSS_RANGE_GROUP, [("Min", 1.0), ("Max", 1.0)]),
   (EVAL_LIMITS_GROUP, [
       ("Eval Min", 1.05), ("Eval Max", 1.05), ("Fail %", 0.9),
       ("Cp", 1.0), ("Cpk", 1.0),
   ]),
]
PDF_NAME_WRAP = 20
PDF_UNIT_WRAP = 14
# Table geometry, as fractions of the table area. A row with n wrapped
# lines is (n + PDF_ROW_PADDING) line-heights tall; pages are split on
# that real height so the last row never runs off the page.
PDF_LINE_HEIGHT = 0.022
PDF_ROW_PADDING = 0.6
PDF_HEADER_LINES = 1.6
PDF_GROUP_HEADER_LINES = 1.4
# Process All output values against the row's Eval limits.
EVAL_PASS_COLOR = "#2e7d32"
EVAL_FAIL_COLOR = "#c62828"
# Heavier rule boxing each column group in the Process All PDF.
PDF_GROUP_BORDER_WIDTH = 1.6

# Process All variables table: (column label, relative PDF width).
PROCESS_ALL_VARIABLE_COLUMNS = [
   ("Var", 0.8), ("Var Name", 2.2), ("Tol Type", 1.1), ("Upper Limit", 1.1),
   ("Lower Limit", 1.1), ("Nominal", 1.1), ("\u00b1 Tol", 1.5),
   ("Tol Band", 1.0), ("Unit", 1.3), ("Distribution", 1.1), ("Cp", 0.7),
]
PDF_VAR_NAME_WRAP = 30

RENDER_STYLES = [
   "Original",
   "Simplified",
   "Expanded",
   "Factored",
   "Cancel",
   "Reduced Fractions",
   "Partial Fractions",
   "Combined Radicals",
   "Power Form",
   "Nsimplify",
]
DEFAULT_RENDER_STYLE = "Original"

DISTRIBUTION_CHART_STYLES = [
   "Histogram",
   "Relative Frequency Histogram",
   "Standardized Relative Histogram",
   "CDF",
]
DEFAULT_CHART_STYLE = "Histogram"
# These two plot each bin as a fraction of the sample count rather than a
# raw count, so they share a y axis label and a percentage formatter.
RELATIVE_CHART_STYLES = (
   "Relative Frequency Histogram",
   "Standardized Relative Histogram",
)

# Python keywords that are values, not names, and may appear in formulas.
PARSER_ALLOWED_KEYWORDS = {"True", "False"}

RESERVED_NAMES = {
   "pi", "sin", "cos", "tan", "exp", "log", "ln", "sqrt", "abs", "Abs"
}

SYMPY_RESERVED_COLLISIONS = [
   "Q", "S", "N", "O", "C", "E", "I", "oo",
   "beta", "gamma", "zeta", "eta", "chi",
]

TRANSFORMATIONS = standard_transformations + (
   implicit_multiplication_application,
   convert_xor,
   split_symbols,
)

ureg = pint.UnitRegistry()

UNIT_PLACEHOLDER_HINT = "e.g. mm, kg, degC (not C), psi  (required)"

# Small flat buttons in the menu bar's corner.
ICON_BUTTON_WIDTH = 28
# Save / Load in the menu bar: short enough to sit inside the bar like the
# icon buttons beside them, with a slightly smaller font to fit.
MENU_BAR_BUTTON_HEIGHT = 22
# The rendered (solved) equation once it no longer matches what's typed.
STALE_EXPRESSION_COLOR = "#b0b0b0"
MENU_BAR_FONT_POINT_SIZE = 8
# Base style for the unit-preview labels; _update_unit_preview appends a
# colour to it once the unit has been resolved.
UNIT_PREVIEW_STYLE = "font-size: 9px;"
TARGET_FIELD_WIDTH = 110

# Field widths in the function and simulation rows.
DESC_FIELD_WIDTH = 180
# The function and subfunction previews share one width: the narrowest
# that shows the widest of them at full size, never wider than the even
# split with the entry field they used to get, and never below this.
MINI_PREVIEW_MIN_WIDTH = 80
# Subfunction rows sit this far right of the function row, to read as
# belonging to it.
SUBFUNCTION_INDENT = 20
COUNT_FIELD_WIDTH = 90
LIMIT_FIELD_WIDTH = 80
# The simulation row's labels sit a point below the section headings.
SIM_LABEL_POINT_SIZE = 9

# lambdify generates Python source and exec()s it, which is slow enough
# to notice: one Process run compiles the same expression for the Monte
# Carlo draw, the OAT sweep, elasticity, and worst-case enumeration. The
# result depends only on the expression and the argument order, so it is
# cached rather than threaded through four call chains as a parameter.
# maxsize is small because only the current expression is ever wanted --
# the cache exists to collapse one run's repeats, not to remember history.
# Gap between a Piecewise branch's formula and its "if" condition, in em.
CASES_COLUMN_GAP_EM = 1.0
# Font size the cases layout is measured at. \\hspace{1} is one font
# size wide, so widths measured here convert to em exactly; a large size
# keeps whole-pixel rounding negligible.
_CASES_MEASURE_SIZE = 100
_cases_parser = MathTextParser("path")


_scratch_figure = None


def measure_equation(latex, font_size, dpi):
   """(width, height) in pixels of an equation drawn exactly as a
   LatexPreview draws it -- same text artist, same Agg renderer, same
   font handling -- on a hidden scratch figure, so a measurement always
   matches what will be drawn."""
   global _scratch_figure
   if _scratch_figure is None:
       _scratch_figure = Figure()
       FigureCanvasAgg(_scratch_figure)
   figure = _scratch_figure
   figure.set_dpi(dpi)
   figure.clear()
   text = figure.text(0, 0, f"${latex}$", fontsize=font_size)
   extent = text.get_window_extent(figure.canvas.get_renderer())
   return extent.width, extent.height


@lru_cache(maxsize=256)
def _stacked_width_em(latex):
   """Rendered width of one cases row, in em, measured in the stacked
   (display-style) context the row is drawn in. 0 if it can't be parsed,
   which just leaves that row unpadded."""
   try:
       width = _cases_parser.parse(
           rf"$\genfrac{{}}{{}}{{0}}{{0}}{{{latex}}}{{{latex}}}$",
           dpi=72, prop=FontProperties(size=_CASES_MEASURE_SIZE),
       )[0]
   except Exception:
       return 0.0
   return width / _CASES_MEASURE_SIZE


class DisplayLatexPrinter(LatexPrinter):
   """LaTeX for the on-screen equation displays.

   matplotlib draws these with its own math renderer, which has no
   \\begin{cases} or array -- the forms sympy uses for Piecewise. It is
   rebuilt here in standard cases notation from what the renderer does
   have: a brace, one branch per line (stacked with \\genfrac at display
   size), and \\hspace padding from measured widths so the formulas
   left-align and the conditions line up in a column.
   """

   def _print_Piecewise(self, expr):
       rows = []
       for branch, cond in expr.args:
           condition = (r"\mathrm{otherwise}" if cond == sympy.true
                        else rf"\mathrm{{if}}\ {self._print(cond)}")
           rows.append((self._print(branch), condition))

       formula_widths = [_stacked_width_em(formula) for formula, _ in rows]
       widest_formula = max(formula_widths)
       lines = [
           rf"{formula}\hspace{{{widest_formula - width + CASES_COLUMN_GAP_EM:.4f}}}{condition}"
           for (formula, condition), width in zip(rows, formula_widths)
       ]
       # \\genfrac centres each line; padding every line out to the widest
       # makes them left-aligned instead.
       line_widths = [_stacked_width_em(line) for line in lines]
       widest_line = max(line_widths)
       lines = [rf"{line}\hspace{{{widest_line - width:.4f}}}"
                for line, width in zip(lines, line_widths)]

       stack = lines[-1]
       for line in reversed(lines[:-1]):
           stack = rf"\genfrac{{}}{{}}{{0}}{{0}}{{{line}}}{{{stack}}}"
       return r"\left\{" + stack + r"\right."


def display_latex(expr):
   """LaTeX for an on-screen equation; see DisplayLatexPrinter."""
   return DisplayLatexPrinter().doprint(expr)


def unit_aware_select(condlist, choicelist, default=np.nan):
   """numpy.select for values that carry units.

   lambdify turns Piecewise into select(conditions, choices). Every value
   here is a pint Quantity, which numpy.select refuses. The choices are
   brought to one unit (a plain number counts as dimensionless), selected
   on their magnitudes, and the unit is put back. A unit mismatch between
   branches raises pint's DimensionalityError, like any other mismatch.
   """
   conds = [np.asarray(getattr(c, "magnitude", c), dtype=bool) for c in condlist]
   units = next((c.units for c in choicelist if isinstance(c, ureg.Quantity)), None)
   if units is None:
       return np.select(conds, choicelist, default=default)
   mags = []
   for choice in choicelist:
       if not isinstance(choice, ureg.Quantity):
           plain = np.asarray(choice, dtype=float)
           # A literal 0 branch -- "no squeeze, no force" -- is zero in
           # any unit, and an undefined (NaN) one has no unit at all, so
           # both take the other branches' unit. Any other plain number
           # is dimensionless, so a real mismatch still raises.
           if np.all((plain == 0) | np.isnan(plain)):
               mags.append(plain)
               continue
       qty = choice if isinstance(choice, ureg.Quantity) else ureg.Quantity(choice, "dimensionless")
       mags.append(np.asarray(qty.to(units).magnitude, dtype=float))
   # A branch can be a constant while another is an array of samples,
   # and the "otherwise" condition is a bare True; numpy.select needs
   # them all at one common shape.
   shape = np.broadcast_shapes(*(c.shape for c in conds), *(m.shape for m in mags))
   conds = [np.broadcast_to(c, shape) for c in conds]
   mags = [np.broadcast_to(m, shape) for m in mags]
   return ureg.Quantity(np.select(conds, mags, default=default), units)


class _NumPyPrinter(NumPyPrinter):
   """numpy code printer for compiled expressions.

   sympy writes a division by zero as ComplexInfinity (zoo) -- e.g. when
   a Piecewise "0" branch ends up in a denominator, as in an elasticity
   x*f'/f. The stock printer has no name for it and refuses to compile.
   It is printed as NaN instead: undefined, which is how every other
   undefined result is already handled. Done here at printing rather
   than by substituting into the expression, which would make sympy
   compare NaN while rebuilding it.
   """

   def _print_ComplexInfinity(self, expr):
       return self._print_NaN(sympy.nan)


@lru_cache(maxsize=8)
def _compiled_expression(symbols, expr):
   # Built with the settings lambdify gives its own default printer, so
   # names come out unqualified ("select", not "numpy.select") and the
   # unit-aware select above is the one called.
   printer = _NumPyPrinter({
       "fully_qualified_modules": False, "inline": True,
       "allow_unknown_functions": True, "user_functions": {},
   })
   return sympy.lambdify(
       symbols, expr, modules=[{"select": unit_aware_select}, "numpy"],
       printer=printer,
   )


def compile_expression(expr, symbol_order):
   return _compiled_expression(tuple(symbol_order), expr)


def new_slot(text="", desc=""):
   """An empty function slot, with the simulation settings it owns."""
   return {
       "text": text,
       "desc": desc,
       "iterations": DEFAULT_ITERATIONS,
       "seed": "",
       "eval_min": "",
       "eval_max": "",
   }


def build_local_dict(expr_text):
   local_dict = {}
   for name in SYMPY_RESERVED_COLLISIONS:
       if re.search(rf"\b{name}\b", expr_text):
           local_dict[name] = sympy.Symbol(name)
   return local_dict


def find_python_keywords(text):
   """Return any Python keywords used as bare names in `text`.

   The parser runs on Python's own tokenizer, so a name like 'lambda'
   or 'in' raises an opaque 'invalid syntax' instead of being read as a
   variable. Detecting them here lets the caller say which word is the
   problem. Word boundaries mean 'sin' is not flagged for containing
   'in', and 'lambda_1' is not flagged either, since the trailing
   underscore makes it a legal identifier.
   """
   found = set()
   for word in keyword.kwlist:
       # True and False can never be variable names, and are needed as
       # values: Piecewise((a, x <= 3), (b, True)) uses True for its
       # "otherwise" branch.
       if word in PARSER_ALLOWED_KEYWORDS:
           continue
       if re.search(rf"\b{word}\b", text):
           found.add(word)
   return sorted(found)


def parse_side(text):
   bad_keywords = find_python_keywords(text)
   if bad_keywords:
       names = ", ".join(f"'{w}'" for w in bad_keywords)
       plural = "s are reserved words" if len(bad_keywords) > 1 else " is a reserved word"
       raise ValueError(
           f"{names}{plural} and cannot be used as a variable name.\n\n"
           f"Add a subscript to make it a valid name — for example "
           f"'{bad_keywords[0]}_1' instead of '{bad_keywords[0]}'."
       )
   local_dict = build_local_dict(text)
   return parse_expr(text, local_dict=local_dict, transformations=TRANSFORMATIONS, evaluate=False)


def lhs_name_of(text):
   """Return the output-variable name defined by an assignment like
   'A = B + C', or None if the text has no '=' or its left side is not a
   single plain name.

   The single judge of what an assignment names, used by the output table,
   the name-collision check, the 'show only used' filter and the report,
   so they cannot disagree about what a row is called.

   Judged as text rather than by parsing. parse_side applies the implicit
   multiplication rules, which belong to the right-hand side: they would
   read a perfectly good output name like 'out' as o*u*t and reject it,
   while the left of an '=' is only ever meant to be one name.
   """
   if "=" not in text:
       return None
   lhs_text = text.split("=", 1)[0].strip()
   if not lhs_text.isidentifier() or keyword.iskeyword(lhs_text):
       return None
   return lhs_text


def find_subfunction_cycle(subfunc_map):
   """Find a circular reference in a {Symbol: expr} subfunction map.

   Returns the cycle as a list of names forming a closed path, e.g.
   ['X', 'Y', 'X'] for X = Y+1 / Y = X+1, or ['A', 'A'] for a
   self-reference. Returns None if the definitions are acyclic.

   This is checked directly rather than inferred from substitution
   failing to converge, so a cycle is caught before any substitution
   runs instead of after producing a half-resolved expression.
   """
   deps = {
       str(name): sorted(
           str(s) for s in rhs.free_symbols if s in subfunc_map
       )
       for name, rhs in subfunc_map.items()
   }

   UNVISITED, IN_PROGRESS, DONE = 0, 1, 2
   state = {name: UNVISITED for name in deps}
   path = []

   def visit(name):
       state[name] = IN_PROGRESS
       path.append(name)
       for dep in deps[name]:
           if state[dep] == IN_PROGRESS:
               return path[path.index(dep):] + [dep]
           if state[dep] == UNVISITED:
               found = visit(dep)
               if found:
                   return found
       state[name] = DONE
       path.pop()
       return None

   for name in sorted(deps):
       if state[name] == UNVISITED:
           found = visit(name)
           if found:
               return found
   return None


def used_subfunction_names(main_expr, subfunc_map):
   """Names of the subfunctions the main expression actually depends on.

   Walks outward from the main expression so a subfunction reached only
   through another one still counts. `seen` makes it terminate whatever
   the definitions look like, which matters because this runs before the
   cycle check has necessarily been consulted.
   """
   used = set()
   seen = set()
   frontier = set(main_expr.free_symbols)
   while frontier:
       symbol = frontier.pop()
       if symbol in seen:
           continue
       seen.add(symbol)
       if symbol in subfunc_map:
           used.add(str(symbol))
           frontier |= subfunc_map[symbol].free_symbols
   return used


def resolve_unit_preview(unit_text):
   unit_text = unit_text.strip()
   if not unit_text:
       return "", True
   try:
       quantity = ureg(unit_text)
       dim = quantity.dimensionality
       if not dim:
           return "dimensionless", True
       return str(dim), True
   except pint.UndefinedUnitError:
       return "\u26a0 unrecognized unit", False
   except Exception:
       return "\u26a0 invalid unit", False


def to_calculation_units(quantity):
   try:
       if quantity.dimensionality == ureg.kelvin.dimensionality:
           return quantity.to(ureg.kelvin)
   except Exception:
       pass
   return quantity


def format_number(value):
   """Format a float without floating-point representation noise
   (e.g. avoid '1.3439999999999999' for what should be '1.344')."""
   if value == 0:
       return "0"
   return f"{value:.10g}"


def tolerance_display(tol_type, upper_text, lower_text, nominal_text):
   """The derived '\u00b1 Tol' and 'Tol Band' texts for one variable.

   Shared by the input table and the Process All variables table so the
   two always agree. Blank where the inputs don't define a value yet.
   """
   # A row with no Tol Type has its limits disabled, so nothing here
   # is meaningful yet.
   if tol_type == "":
       return "", ""

   # A Fixed row's tolerance is zero by definition of the Tol Type, so
   # both displays are stated explicitly rather than left blank. This
   # does not depend on Nominal being filled in yet.
   if tol_type == FIXED_TOL_TYPE:
       return "\u00b10", "0"

   try:
       upper = float(upper_text)
       lower = float(lower_text)
   except ValueError:
       return "", ""

   band = format_number(abs(upper - lower))

   try:
       nominal = float(nominal_text)
   except ValueError:
       return "", band

   plus_text = format_number(abs(upper - nominal))
   minus_text = format_number(abs(nominal - lower))

   # Compare the formatted values rather than the raw floats: limits
   # like 10.3/10.1/9.9 differ only by float representation noise
   # (0.19999999999999996 vs 0.20000000000000018) and would otherwise
   # display as an asymmetric band of two identical-looking numbers.
   if plus_text == minus_text:
       return f"\u00b1{plus_text}", band
   return f"+{plus_text} / -{minus_text}", band


def _stats_heading(title):
   """Section heading for the monospace stats block.

   Padded out to STATS_WRAP_WIDTH so the rules line up in the results
   dialog and the exported PDF, which share this text.
   """
   prefix = f"--- {title} "
   return prefix + "-" * max(STATS_WRAP_WIDTH - len(prefix), 3)


def apply_render_style(expr, style):
   """Transform a sympy expression for display according to the selected
   render style. Falls back to the untransformed expression if the
   transform fails on an unusual expression, so a display preference
   can never break Process or crash the app. This only affects what is
   DISPLAYED — the actual expression used for Monte Carlo calculation
   (self.solved_expr) is never modified by this function."""
   try:
       if style == "Simplified":
           return sympy.simplify(expr)
       if style == "Expanded":
           return sympy.expand(expr)
       if style == "Factored":
           return sympy.factor(expr)
       if style == "Cancel":
           return sympy.cancel(expr)
       if style == "Reduced Fractions":
           return sympy.together(expr)
       if style == "Partial Fractions":
           return sympy.apart(expr)
       if style == "Combined Radicals":
           return sympy.radsimp(expr)
       if style == "Power Form":
           return sympy.powsimp(expr, force=True)
       if style == "Nsimplify":
           return sympy.nsimplify(expr, rational=False)
   except Exception:
       pass
   return expr  # "Original" or any failed transform falls back to as-is


# ----------------------------------------------------------------------
# Report number formatting
# ----------------------------------------------------------------------

def format_report_number(value, max_decimals):
   """Report display: round to at most `max_decimals` decimal places and
   drop trailing zeros, so 0.125 stays 0.125 while 18.491403 becomes
   18.4914 at 4 places. Never uses scientific notation."""
   value = float(value)
   if not np.isfinite(value):
       return str(value)
   text = f"{value:.{max_decimals}f}"
   if "." in text:
       text = text.rstrip("0").rstrip(".")
   return "0" if text == "-0" else text


def process_all_layout(include_eval_limits):
   """Column layout for one Process All report. The Eval Limits group is
   left out when no processed function has an Eval limit."""
   groups = [
       (label, columns) for label, columns in PROCESS_ALL_COLUMN_GROUPS
       if include_eval_limits or label != EVAL_LIMITS_GROUP
   ]
   headers, weights, spans = [], [], []
   col = 0
   for label, columns in groups:
       spans.append((label, col, len(columns)))
       headers += [name for name, _ in columns]
       weights += [weight for _, weight in columns]
       col += len(columns)
   return {
       "groups": [label for label, _ in groups],
       "headers": headers,
       "weights": weights,
       "spans": spans,
   }


# ----------------------------------------------------------------------
# Calculation core — no widgets and no popups. Process and Process All
# both run a function through these, so a slot gives the same answer
# whichever button runs it. Failures are raised as CalculationError with
# user-facing text; each caller decides how to show them.
# ----------------------------------------------------------------------

class CalculationError(Exception):
   """A failure that stops one function from being calculated. `title`
   and `message` are written for the user, so a caller can put them
   straight into a popup or a report."""

   def __init__(self, title, message):
       super().__init__(message)
       self.title = title
       self.message = message


class MainFunctionParseError(CalculationError):
   """The main function's right-hand side could not be parsed."""


class SubfunctionParseError(CalculationError):
   """One subfunction row could not be parsed. `row_index` (0-based)
   lets the UI mark that row's preview."""

   def __init__(self, title, message, row_index):
       super().__init__(title, message)
       self.row_index = row_index


# Comparison operators typed in the wrong order, and what was meant.
_COMPARISON_TYPOS = {"=>": ">=", "=<": "<="}


def comparison_typo_message(text):
   """A clear message if `text` uses '=>' or '=<', else None. Python
   reports these only as 'invalid syntax', which doesn't say where."""
   for typo, meant in _COMPARISON_TYPOS.items():
       if typo in text:
           return (f"'{typo}' is not a comparison. Write '{meant}' instead "
                   f"(the '=' goes second: '>=' is greater than or equal, "
                   f"'<=' is less than or equal).")
   return None


def parse_main_function(raw_text):
   """Split 'Name = expression' and parse the expression.
   Returns (lhs_name or None, main_expr)."""
   expr_text = raw_text
   lhs_name = None
   if "=" in raw_text:
       lhs_part, expr_text = raw_text.split("=", 1)
       expr_text = expr_text.strip()
       if lhs_part.strip():
           # lhs_name_of is the one judge of what an assignment names,
           # so the output table, the collision check and the report can
           # never disagree. It returns None for anything that is not a
           # single plain name, which is rejected here rather than
           # passed to sympy.Symbol() as a literal.
           lhs_name = lhs_name_of(raw_text)
           if lhs_name is None:
               raise CalculationError(
                   "Parse Error",
                   f"'{lhs_part.strip()}' is not a valid output variable "
                   "name.\n\nThe left of the '=' must be a single name, "
                   "like 'P' or 'P_out'."
               )
   typo = comparison_typo_message(expr_text)
   if typo:
       raise MainFunctionParseError("Parse Error", f"Could not parse the main function:\n{typo}")
   try:
       main_expr = parse_side(expr_text)
   except Exception as exc:
       raise MainFunctionParseError(
           "Parse Error", f"Could not parse the main function:\n{exc}"
       )
   return lhs_name, main_expr


def parse_subfunctions(sub_texts):
   """Parse subfunction rows into a {Symbol: expr} map.

   Returns (subfunc_map, parsed_rows) where parsed_rows[i] is the
   (lhs_expr, rhs_expr) pair for row i, or None for a blank row."""
   subfunc_map = {}
   parsed_rows = []
   for idx, sub_text in enumerate(sub_texts):
       sub_text = sub_text.strip()
       if not sub_text:
           parsed_rows.append(None)
           continue
       if "=" not in sub_text:
           raise SubfunctionParseError(
               "Parse Error",
               f"Subfunction '{sub_text}' is missing an '=' sign.", idx
           )

       typo = comparison_typo_message(sub_text)
       if typo:
           raise SubfunctionParseError(
               "Parse Error", f"Could not parse subfunction '{sub_text}':\n{typo}", idx
           )

       sub_lhs_text, sub_rhs_text = sub_text.split("=", 1)
       sub_lhs_text = sub_lhs_text.strip()
       try:
           sub_lhs_expr = parse_side(sub_lhs_text)
           sub_rhs_expr = parse_side(sub_rhs_text.strip())
       except Exception as exc:
           raise SubfunctionParseError(
               "Parse Error",
               f"Could not parse subfunction '{sub_text}':\n{exc}", idx
           )

       if not isinstance(sub_lhs_expr, sympy.Symbol):
           raise SubfunctionParseError(
               "Parse Error",
               f"Subfunction left side must be a single variable name, "
               f"got: '{sub_lhs_text}'", idx
           )

       subfunc_map[sub_lhs_expr] = sub_rhs_expr
       parsed_rows.append((sub_lhs_expr, sub_rhs_expr))
   return subfunc_map, parsed_rows


def check_subfunction_cycle(subfunc_map):
   cycle = find_subfunction_cycle(subfunc_map)
   if cycle:
       raise CalculationError(
           "Circular Subfunction",
           "These subfunctions define each other in a loop, so they can "
           "never be reduced to input variables:\n\n"
           "    " + " \u2192 ".join(cycle) + "\n\n"
           "Break the loop by rewriting one of them in terms of input "
           "variables only."
       )


def solve_function(main_expr, subfunc_map):
   """Substitute subfunctions until only input variables remain.
   Returns (solved_expr, sorted input variable names).

   Call check_subfunction_cycle first. One pass resolves one level of
   nesting, and acyclic definitions are at most every subfunction in a
   line -- len(map) passes. No arbitrary ceiling and no failure case."""
   solved_expr = main_expr
   for _ in range(len(subfunc_map)):
       new_expr = solved_expr.subs(subfunc_map, simultaneous=True)
       if new_expr == solved_expr:
           break
       solved_expr = new_expr

   disallowed_present = sorted(
       {str(s) for s in solved_expr.free_symbols if str(s) in RESERVED_NAMES}
   )
   if disallowed_present:
       raise CalculationError(
           "Unable to Render",
           "The following variable name(s) are not allowed because they "
           "collide with built-in math functions or constants:\n\n"
           + ", ".join(disallowed_present) +
           "\n\nPlease rename these variables (e.g. add a subscript, like "
           "'sin_1' instead of 'sin') and try again."
       )

   variables = sorted(
       (str(s) for s in solved_expr.free_symbols if str(s) not in RESERVED_NAMES),
       key=str.lower
   )
   return solved_expr, variables


def describe_range(value_set, symbol):
   """A set of values as written by hand: '3 < X_s \u2264 4', 'X_s > 7',
   several pieces joined with 'or'."""
   name = str(symbol)
   pieces = value_set.args if isinstance(value_set, sympy.Union) else (value_set,)
   parts = []
   for piece in pieces:
       if isinstance(piece, sympy.FiniteSet):
           parts.append(" or ".join(f"{name} = {sympy.sstr(v)}" for v in piece))
           continue
       if not isinstance(piece, sympy.Interval):
           parts.append(sympy.sstr(piece.as_relational(symbol)))
           continue
       lo, hi = piece.start, piece.end
       lo_op = "<" if piece.left_open else "\u2264"
       hi_op = "<" if piece.right_open else "\u2264"
       if lo == sympy.S.NegativeInfinity and hi == sympy.S.Infinity:
           parts.append(f"every value of {name}")
       elif lo == sympy.S.NegativeInfinity:
           parts.append(f"{name} {hi_op} {sympy.sstr(hi)}")
       elif hi == sympy.S.Infinity:
           above_op = ">" if piece.left_open else "\u2265"
           parts.append(f"{name} {above_op} {sympy.sstr(lo)}")
       else:
           parts.append(f"{sympy.sstr(lo)} {lo_op} {name} {hi_op} {sympy.sstr(hi)}")
   return " or ".join(parts)


def piecewise_gap_warnings(named_exprs):
   """Warnings for Piecewise formulas that leave some values with no
   branch -- those values are undefined (NaN) when sampled.

   `named_exprs` is (name, expr) pairs, each formula as written (before
   subfunction substitution), so a condition like X_s <= 3 is still in
   terms of one variable and the uncovered range can be stated exactly.
   A Piecewise that ends with a True ("otherwise") branch, or whose
   conditions together cover every value, gets no warning.
   """
   warnings = []
   for name, expr in named_exprs:
       for piecewise in expr.atoms(sympy.Piecewise):
           conds = [cond for _, cond in piecewise.args]
           if conds[-1] == sympy.true:
               continue
           symbols = set().union(*(cond.free_symbols for cond in conds))
           uncovered = None
           if len(symbols) == 1:
               symbol = next(iter(symbols))
               try:
                   covered = sympy.Union(*(cond.as_set() for cond in conds))
                   uncovered = sympy.Complement(sympy.S.Reals, covered)
               except Exception:
                   uncovered = None
               if uncovered == sympy.S.EmptySet:
                   continue
           if uncovered is not None:
               where = describe_range(uncovered, symbol)
               warnings.append(
                   f"{name}: no branch covers {where}, so the result is "
                   f"undefined (NaN) there."
               )
           else:
               warnings.append(
                   f"{name}: the last branch isn't an 'otherwise' (True) "
                   f"branch, so any value that matches none of the conditions "
                   f"is undefined (NaN)."
               )
   return warnings


# Above this size, full sympy.simplify() can run for minutes (a Piecewise
# raised to powers is the usual case), so it is skipped -- see
# elasticity_ratio.
ELASTICITY_SIMPLIFY_MAX_OPS = 150
# The cheaper cancel step proves power laws constant; those are small
# expressions, so it is skipped above this size too (on a ~1,500-op
# Piecewise formula it costs seconds per variable and reduces nothing).
ELASTICITY_CANCEL_MAX_OPS = 400


def elasticity_ratio(expr, symbol):
   """The elasticity x * (df/dx) / f, reduced to a number when it is one.

   Only the "is it an exact constant" question needs simplifying -- the
   values are the same either way. Cheap, always-bounded steps (combine
   powers, cancel) first: they settle power laws such as x^2 -> 2. Full
   simplify() is tried only on small expressions, where it is fast.

   A reduced form is used only when it is a finite constant. Otherwise
   the unreduced ratio is returned and evaluated numerically: it is the
   same function, and reducing can introduce artefacts the original
   never has -- cancelling a Piecewise's "0" branch against its own
   derivative leaves a symbolic 0/0 (ComplexInfinity) that can't be
   evaluated.
   """
   raw = symbol * sympy.diff(expr, symbol) / expr
   size = sympy.count_ops(raw)

   def finite_constant(candidate):
       return (not candidate.free_symbols
               and not candidate.has(sympy.zoo, sympy.nan, sympy.oo, -sympy.oo))

   if size <= ELASTICITY_CANCEL_MAX_OPS:
       try:
           reduced = sympy.cancel(sympy.powsimp(raw))
           if finite_constant(reduced):
               return reduced
       except Exception:
           pass
   if size <= ELASTICITY_SIMPLIFY_MAX_OPS:
       simplified = sympy.simplify(raw)
       if finite_constant(simplified):
           return simplified
   return raw


def solved_function_text(lhs_name, solved_expr, render_style):
   """The solved expression as plain text for reports, styled to match
   the on-screen render."""
   body = sympy.sstr(apply_render_style(solved_expr, render_style))
   return f"{lhs_name} = {body}" if lhs_name else body


def _is_number(text):
   try:
       float(text)
       return True
   except ValueError:
       return False


def parse_cp(text):
   """Cp as a number, falling back to the default when blank or invalid.

   Out-of-range values fall back too rather than being clamped: the
   validator already rejects them as you type, so a value that gets
   here is a paste or a load, and silently treating 50 as 10 would be
   worse than treating it as unspecified.
   """
   try:
       value = float(str(text).strip())
   except ValueError:
       return DEFAULT_CP
   return value if MIN_CP <= value <= MAX_CP else DEFAULT_CP


def variable_missing_reason(var_name, data):
   """Why an input variable's stored data can't be simulated, or None.
   `data` uses the master_var_data layout."""
   tol = data.get("tol", "")
   upper = data.get("upper", "").strip()
   lower = data.get("lower", "").strip()
   nominal = data.get("nominal", "").strip()
   unit = data.get("unit", "").strip()

   if tol == "":
       return f"'{var_name}': Tol Type is not selected."
   if tol == FIXED_TOL_TYPE:
       if not nominal:
           return f"'{var_name}': Nominal is required for a Fixed variable."
   else:
       if not upper:
           return f"'{var_name}': Upper Limit is missing."
       if not _is_number(upper):
           return f"'{var_name}': Upper Limit '{upper}' is not a number."
       if not lower:
           return f"'{var_name}': Lower Limit is missing."
       if not _is_number(lower):
           return f"'{var_name}': Lower Limit '{lower}' is not a number."
       if tol == "Asymmetric" and not nominal:
           return f"'{var_name}': Nominal is required for Asymmetric tolerance."
       dist = data.get("dist", "")
       if dist not in VARIABLE_DISTRIBUTIONS:
           return (f"'{var_name}': Distribution '{dist}' is not available "
                   f"\u2014 choose one of {', '.join(VARIABLE_DISTRIBUTIONS)}, "
                   f"or set Tol Type to {FIXED_TOL_TYPE} for a single value.")
   if not _is_number(nominal):
       return f"'{var_name}': Nominal '{nominal}' is not a number."
   if not unit:
       return f"'{var_name}': Unit is missing."
   _, is_valid = resolve_unit_preview(unit)
   if not is_valid:
       return f"'{var_name}': Unit '{unit}' is not recognized."
   return None


def output_missing_reason(data):
   """Why the output variable's stored data can't be used, or None.
   `data` uses the master_output_data layout."""
   unit = data.get("unit", "").strip()
   if not unit:
       return "Output Variable's Unit is required."
   _, is_valid = resolve_unit_preview(unit)
   if not is_valid:
       return f"Output Variable's Unit '{unit}' is not recognized."
   return None


def parse_eval_limits(min_text, max_text):
   """Returns (eval_min, eval_max), each a float or None.
   Raises ValueError with a user-facing message."""
   limits = []
   for label, text in (("Eval Min", min_text), ("Eval Max", max_text)):
       text = text.strip()
       if not text:
           limits.append(None)
       elif _is_number(text):
           limits.append(float(text))
       else:
           raise ValueError(f"{label} '{text}' is not a number.")
   eval_min, eval_max = limits
   if eval_min is not None and eval_max is not None and eval_min > eval_max:
       raise ValueError("Eval Min cannot exceed Eval Max.")
   return eval_min, eval_max


def parse_run_settings(iterations_text, seed_text):
   """Returns (n_iter, seed or None). Raises ValueError with a
   user-facing message."""
   try:
       n_iter = int(iterations_text.strip())
   except ValueError:
       n_iter = 0
   if n_iter <= 0:
       raise ValueError("Iterations must be a positive integer.")
   seed_text = seed_text.strip()
   if not seed_text:
       return n_iter, None
   try:
       seed = int(seed_text)
   except ValueError:
       seed = -1
   if seed < 0:
       raise ValueError(f"Seed '{seed_text}' must be a whole number 0 or greater.")
   return n_iter, seed


def build_var_info(variables, var_data):
   """Numeric simulation inputs for each variable, from master_var_data.
   Callers check variable_missing_reason first."""
   var_info = []
   for var in variables:
       data = var_data[var]
       fixed = data.get("tol") == FIXED_TOL_TYPE
       dist = FIXED_DISTRIBUTION if fixed else data.get("dist", VARIABLE_DISTRIBUTIONS[0])
       nominal = float(data["nominal"])
       # A Fixed variable has no limits to read. Collapsing them onto
       # Nominal keeps every downstream analysis consistent with the
       # Monte Carlo draw: zero OAT swing, zero RSS contribution, and a
       # worst-case corner that sits at Nominal.
       if fixed:
           lower = upper = nominal
       else:
           lower = float(data["lower"])
           upper = float(data["upper"])
       # Cp only shapes the distributions that derive sigma from the
       # limits; for the others the limits are the spread, so an
       # effective Cp of 1 keeps RSS consistent with the sampler.
       cp = parse_cp(data.get("cp", "")) if dist in CP_AWARE_DISTRIBUTIONS else DEFAULT_CP
       var_info.append({
           "var": var, "dist": dist, "unit": data["unit"].strip(),
           "nominal": nominal, "lower": lower, "upper": upper,
           "fixed": fixed, "cp": cp,
       })
   return var_info


def eval_failure_stats(values, eval_min, eval_max):
   """Counts and percentages of samples outside Eval Min / Eval Max."""
   n = len(values)
   below = int(np.sum(values < eval_min)) if eval_min is not None else None
   above = int(np.sum(values > eval_max)) if eval_max is not None else None
   pct_total_fail = None
   if eval_min is not None or eval_max is not None:
       pct_total_fail = 100.0 * ((below or 0) + (above or 0)) / n
   return {
       "below": below,
       "above": above,
       "pct_below": (100.0 * below / n) if below is not None else None,
       "pct_above": (100.0 * above / n) if above is not None else None,
       "pct_total_fail": pct_total_fail,
   }


# ----------------------------------------------------------------------
# Widgets
# ----------------------------------------------------------------------

class LatexPreview(QScrollArea):
   """Equation display that shrinks to fit, then scrolls rather than clip.

   Three stages, in order:
     1. render at max_font_size;
     2. if too wide or too tall, step the font down to min_font_size;
     3. if still too big at the floor, grow the canvas to the equation's
        natural size in that direction so a scrollbar appears.

   Height matters as well as width: a Piecewise written in cases
   notation stacks one branch per line.

   Nothing is ever silently cut off, and the scrollbar only shows up for an
   equation long enough to earn it.

   The resize trigger lives on this scroll area, not on the canvas: stage 3
   sets the canvas width, so re-rendering from the canvas's own resizeEvent
   would feed back into itself. The viewport width is an input to the
   render and is never written by it.
   """

   # Pixels of breathing room reserved at each end of the equation.
   INSET = 6
   # And above and below it.
   VINSET = 1

   def __init__(self, height, max_font_size, min_font_size, centered=False):
       super().__init__()
       self.max_font_size = max_font_size
       self.min_font_size = min_font_size
       self.centered = centered
       self.latex_str = ""
       self._rendering = False
       self.text_color = "black"

       self.figure = Figure()
       self.canvas = FigureCanvas(self.figure)
       self.setWidget(self.canvas)
       self.setWidgetResizable(False)   # stage 3 controls the canvas width
       self.setFrameShape(QFrame.NoFrame)
       self.setFixedHeight(height)
       self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
       self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

   # -- triggers ---------------------------------------------------------- #
   def resizeEvent(self, event):
       super().resizeEvent(event)
       self._render()

   # -- public ------------------------------------------------------------ #
   def set_latex(self, latex_str):
       self.latex_str = latex_str or ""
       self._render()

   def natural_width(self):
       """Width needed to show the current equation without shrinking it
       for width, insets included; 0 when empty.

       Measured at the font size the equation will actually be drawn at
       for this preview's height: the largest size, from max_font_size
       down to min_font_size, at which it fits vertically -- the same
       height rule _render_stages applies. A Piecewise stacks its branches
       on separate lines, so it is drawn smaller than max_font_size, and
       measuring it at full size would overstate its width by half again.
       """
       if not self.latex_str:
           return 0
       available_height = self._content_height(False) - 2 * self.VINSET
       size = self.max_font_size
       while True:
           try:
               width, height = measure_equation(self.latex_str, size, self.figure.dpi)
           except Exception:
               return 0
           if height <= available_height or size <= self.min_font_size:
               break
           size -= 1
       needed = int(math.ceil(width)) + 2 * self.INSET + 2
       # Still too tall at the smallest size: it will scroll vertically
       # (stage 3), and that scrollbar takes width from the equation.
       if height > available_height:
           needed += self.verticalScrollBar().sizeHint().width()
       return needed

   def set_text_color(self, color):
       """Recolour the equation in place -- no re-layout -- and keep the
       colour for later renders."""
       if color == self.text_color:
           return
       self.text_color = color
       axes = self.figure.axes
       if axes and axes[0].texts and self.latex_str:
           axes[0].texts[0].set_color(color)
           self.canvas.draw_idle()

   def font_size(self):
       """Current font size, or None when nothing is displayed."""
       axes = self.figure.axes
       if not axes or not axes[0].texts:
           return None
       return axes[0].texts[0].get_fontsize()

   # -- rendering --------------------------------------------------------- #
   def _render(self):
       if self._rendering:
           return
       self._rendering = True
       try:
           self._render_stages()
       except Exception:
           # Mathtext is parsed at draw time, so a malformed expression
           # raises from canvas.draw() rather than from ax.text().
           try:
               self._draw("Unable to render expression", self.min_font_size,
                          max(self.viewport().width(), 1), plain=True)
           except Exception:
               pass
       finally:
           self._rendering = False

   def _render_stages(self):
       # The preview's full width, not the viewport's: the viewport is
       # already narrowed while a scrollbar from the previous draw is still
       # showing, and stage 3 below subtracts the vertical scrollbar itself
       # -- starting from the viewport counted it twice.
       width = max(self.contentsRect().width(), 1)

       if not self.latex_str:
           self._draw(None, self.max_font_size, width, scrolling=False)
           return

       # Stage 1 and 2: fit by shrinking, in both directions.
       text = self._draw(self.latex_str, self.max_font_size, width,
                         scrolling=False)
       available_width = width - 2 * self.INSET
       available_height = self._content_height(False) - 2 * self.VINSET
       renderer = self.canvas.get_renderer()
       size = self.max_font_size
       text_width, text_height = self._text_size(text, renderer)

       while ((text_width > available_width or text_height > available_height)
              and size > self.min_font_size):
           size -= 1
           text.set_fontsize(size)
           text_width, text_height = self._text_size(text, renderer)

       too_wide = text_width > available_width
       too_tall = text_height > available_height
       if not too_wide and not too_tall:
           self.canvas.draw()
           return

       # Stage 3: at the floor and still too big, so give the canvas the
       # size the equation actually needs in that direction and let the
       # scrollbar handle it. Left-aligned when too wide -- centring
       # something wider than the viewport would hide its start. A too-wide
       # canvas is also shortened by the horizontal scrollbar's height, and
       # a too-tall one narrowed by the vertical scrollbar's width, so the
       # one scrollbar never forces the other.
       canvas_width = (int(text_width) + 2 * self.INSET if too_wide
                       else width - self.verticalScrollBar().sizeHint().width())
       canvas_height = (int(text_height) + 2 * self.VINSET if too_tall else None)
       self._draw(self.latex_str, size, canvas_width, scrolling=too_wide,
                  force_left=too_wide, height=canvas_height)

   def _content_height(self, scrolling):
       """Canvas height, computed rather than read back from the viewport.

       Reading viewport().height() would mean re-rendering whenever a
       scrollbar appears, and reacting to viewport resizes is unsafe while
       the widget is being torn down.
       """
       height = self.height()
       if scrolling:
           height -= self.horizontalScrollBar().sizeHint().height()
       return max(height, 1)

   def _draw(self, latex_str, font_size, width, scrolling=False,
             plain=False, force_left=False, height=None):
       """Size the canvas, place the text, return the text artist.
       height overrides the viewport-derived height (stage 3, too tall)."""
       if height is None:
           height = self._content_height(scrolling)
       self.canvas.setFixedSize(int(width), height)
       self.figure.set_size_inches(width / self.figure.dpi,
                                   height / self.figure.dpi)
       self.figure.clear()
       # add_axes, not add_subplot, so no width is lost to tick-label
       # margins on an axis whose ticks are off.
       ax = self.figure.add_axes((0.0, 0.0, 1.0, 1.0))
       ax.axis("off")

       if latex_str is None:
           self.canvas.draw()
           return None

       centered = self.centered and not force_left
       x = 0.5 if centered else self.INSET / float(width)
       body = latex_str if plain else f"${latex_str}$"
       text = ax.text(x, 0.5, body, fontsize=font_size,
                      ha="center" if centered else "left", va="center",
                      color="red" if plain else self.text_color,
                      transform=ax.transAxes)
       self.canvas.draw()
       return text

   def _text_size(self, text, renderer):
       if text is None:
           return 0.0, 0.0
       extent = text.get_window_extent(renderer)
       return extent.width, extent.height


# ----------------------------------------------------------------------
# Widget factories
#
# Qt construction is four or five statements per widget -- validator,
# placeholder, width, tooltip, signal -- and the same four or five
# repeated across two tables, a settings dialog and an allocation dialog.
# These keep the layout code reading as layout instead of as a list of
# setters, and they are the one place a shared look changes.
#
# Deliberately thin: each takes exactly the arguments this app varies and
# returns a plain widget. Anything a single caller needs is still set by
# that caller afterwards.
# ----------------------------------------------------------------------
def bold_label(text, point_size=10):
   label = QLabel(text)
   label.setFont(QFont("", point_size, QFont.Bold))
   return label


def note_label(text, color="gray", point_size=9):
   """Small gray explanatory text under a control."""
   label = QLabel(text)
   label.setTextFormat(Qt.RichText)
   label.setWordWrap(True)
   label.setStyleSheet(f"color: {color}; font-size: {point_size}px;")
   return label


def icon_button(symbol, tooltip, on_click=None, width=ICON_BUTTON_WIDTH,
                flat=True):
   """on_click is optional: a row's remove button cannot be connected until
   the entry it removes has been built."""
   button = QPushButton(symbol)
   button.setFixedWidth(width)
   button.setFlat(flat)
   button.setToolTip(tooltip)
   if on_click is not None:
       button.clicked.connect(on_click)
   return button


def line_edit_text_width(edit, text):
   """Width a QLineEdit needs to show `text` in full, as its own style
   computes it (frame, margins), so it is right for any platform/font."""
   style = edit.style()
   metrics = edit.fontMetrics()
   margins = edit.textMargins()
   text_size = QSize(metrics.horizontalAdvance(text) + margins.left() + margins.right() + 4,
                     metrics.height())
   option = QStyleOptionFrame()
   option.initFrom(edit)
   option.lineWidth = style.pixelMetric(QStyle.PM_DefaultFrameWidth, option, edit)
   return style.sizeFromContents(QStyle.CT_LineEdit, option, text_size, edit).width()


def text_button(text, on_click, tooltip=None):
   button = QPushButton(text)
   button.clicked.connect(on_click)
   if tooltip:
       button.setToolTip(tooltip)
   return button


def double_validator():
   """Plain decimal notation, so 1e5 is not silently accepted as valid."""
   validator = QDoubleValidator()
   validator.setNotation(QDoubleValidator.StandardNotation)
   return validator


def line_edit(text="", placeholder=None, validator=None, width=None,
              tooltip=None, enabled=True, on_change=None):
   edit = QLineEdit(text)
   if placeholder is not None:
       edit.setPlaceholderText(placeholder)
   if validator is not None:
       # Ownership passes to the edit, so a validator built per field is
       # not at risk of being collected out from under it.
       edit.setValidator(validator)
   if width is not None:
       edit.setMaximumWidth(width)
   if tooltip is not None:
       edit.setToolTip(tooltip)
   if not enabled:
       edit.setEnabled(False)
   if on_change is not None:
       edit.textChanged.connect(on_change)
   return edit


def derived_display(tooltip):
   """A field the app fills in and the user cannot: always read-only."""
   edit = QLineEdit("")
   edit.setReadOnly(True)
   edit.setEnabled(False)
   edit.setAlignment(Qt.AlignCenter)
   edit.setToolTip(tooltip)
   return edit


def unit_preview_label():
   label = QLabel("")
   label.setWordWrap(True)
   label.setStyleSheet(UNIT_PREVIEW_STYLE)
   return label


def choice_box(items, current=None, on_change=None):
   combo = NoScrollComboBox()
   combo.addItems(items)
   if current:
       index = combo.findText(current)
       if index >= 0:
           combo.setCurrentIndex(index)
   if on_change is not None:
       combo.currentTextChanged.connect(on_change)
   return combo


class VarRow:
   """Every widget of one Input Variables row, fetched in one place.

   Eleven methods used to open with the same block of cellWidget lookups
   and their own idea of how thoroughly to check for None, which is how
   they drifted apart -- some checked every widget, some checked one, some
   relied on a ValueError further down. This does the lookups once and
   answers None-safely, so a caller only has to decide what to do about an
   unbuilt row, not remember to ask.

   Text is returned exactly as typed. Callers that want it trimmed strip
   it themselves, because two of them (the master store and the saved
   project) deliberately keep what the user entered.
   """

   WIDGETS = {
       "name": COL_NAME,
       "tol": COL_TOL,
       "upper": COL_UPPER,
       "lower": COL_LOWER,
       "nominal": COL_NOMINAL,
       "deviation": COL_DEVIATION,
       "band": COL_BAND,
       "unit": COL_UNIT,
       "dist": COL_DIST,
       "cp": COL_CP,
   }

   def __init__(self, table, row):
       self._item = table.item(row, COL_VAR)
       for attr, col in self.WIDGETS.items():
           setattr(self, attr, table.cellWidget(row, col))

   @property
   def built(self):
       """True once Qt holds every widget this row is read through.

       A row is populated column by column, and several handlers fire
       during that pass, so any of them can arrive early.
       """
       return (self._item is not None
               and all(getattr(self, attr) is not None for attr in self.WIDGETS))

   @property
   def var(self):
       return self._item.text() if self._item is not None else ""

   def _text(self, attr):
       widget = getattr(self, attr)
       return widget.text() if widget is not None else ""

   @property
   def name_text(self):
       return self._text("name")

   @property
   def upper_text(self):
       return self._text("upper")

   @property
   def lower_text(self):
       return self._text("lower")

   @property
   def nominal_text(self):
       return self._text("nominal")

   @property
   def unit_text(self):
       return self._text("unit")

   @property
   def cp_text(self):
       return self._text("cp")

   @property
   def tol_type(self):
       return self.tol.currentText() if self.tol is not None else ""

   @property
   def dist_type(self):
       return self.dist.currentText() if self.dist is not None else ""

   @property
   def is_fixed(self):
       return self.tol_type == FIXED_TOL_TYPE

   @property
   def cp_value(self):
       return parse_cp(self.cp_text)

   def as_data(self):
       """This row in master_var_data layout -- the one form both the
       on-screen readiness check and Process All validate."""
       return {
           "name": self.name_text,
           "tol": self.tol_type,
           "upper": self.upper_text,
           "lower": self.lower_text,
           "nominal": self.nominal_text,
           "unit": self.unit_text,
           "dist": self.dist_type or DISTRIBUTIONS[0],
           "cp": self.cp_text or format_number(DEFAULT_CP),
       }

   def set_text(self, attr, text, block_signals=False):
       widget = getattr(self, attr)
       if widget is None:
           return
       if not block_signals:
           widget.setText(text)
           return
       widget.blockSignals(True)
       widget.setText(text)
       widget.blockSignals(False)

   def signature(self):
       """The fields a Monte Carlo run actually consumes, for change detection."""
       return (self.var, self.tol_type, self.dist_type,
               self.unit_text.strip(), self.nominal_text.strip(),
               self.lower_text.strip(), self.upper_text.strip(),
               self.cp_text.strip())


class GroupedHeaderView(QHeaderView):
   """Two-row table header: group labels spanning their columns on top,
   the normal column labels below. Scrolls and resizes with the table."""

   def __init__(self, group_spans, parent=None):
       super().__init__(Qt.Horizontal, parent)
       self._group_of = {}
       for label, start, count in group_spans:
           for col in range(start, start + count):
               self._group_of[col] = (label, start, count)
       self.setSectionsClickable(False)
       self.setDefaultAlignment(Qt.AlignCenter)

   def sizeHint(self):
       size = super().sizeHint()
       size.setHeight(size.height() * 2)
       return size

   def _draw_cell(self, painter, rect, text, bold):
       option = QStyleOptionHeader()
       option.initFrom(self)
       option.rect = rect
       option.text = text
       option.textAlignment = Qt.AlignCenter
       option.position = QStyleOptionHeader.Middle
       painter.save()
       if bold:
           font = painter.font()
           font.setBold(True)
           painter.setFont(font)
       self.style().drawControl(QStyle.CE_Header, option, painter, self)
       painter.restore()

   def paintSection(self, painter, rect, logical_index):
       half = rect.height() // 2
       label_rect = QRect(rect.left(), rect.top() + half, rect.width(), rect.height() - half)
       text = self.model().headerData(logical_index, Qt.Horizontal, Qt.DisplayRole)
       self._draw_cell(painter, label_rect, str(text or ""), bold=False)

       group = self._group_of.get(logical_index)
       if group is None:
           return
       label, start, count = group
       left = self.sectionViewportPosition(start)
       width = sum(self.sectionSize(col) for col in range(start, start + count))
       # Every section in the group redraws the whole span, so the label
       # is complete whichever part of the header gets repainted.
       self._draw_cell(painter, QRect(left, rect.top(), width, half), label, bold=True)


class NoScrollComboBox(QComboBox):
   """A combo box that ignores the mouse wheel.

   Qt's default is to change the selection on wheel events even when the
   box has no focus, so scrolling a long form or table silently rewrites
   whatever dropdown the cursor happened to pass over. Passing the event
   to the parent instead lets the scroll do what the user meant -- scroll
   -- and leaves the selection changeable only by clicking or by keyboard
   once the box is focused.
   """

   def wheelEvent(self, event):
       event.ignore()


# ----------------------------------------------------------------------
# Main Window
# ----------------------------------------------------------------------

class FunctionVariableTool(QMainWindow):
   def __init__(self):
       super().__init__()
       self.setWindowTitle(f"Montecarlo Tool V{APP_VERSION}")
       self.resize(1450, 900)

       self.solved_expr = None
       self.subfunction_rows = []

       # Names of subfunctions actually reached by the last successful
       # render, computed transitively from the main expression. None
       # means "nothing rendered yet", which shows every row.
       self._used_subfunction_names = None

       # Input variables of the last successful render, in table order.
       # The input table always lists these first; with "Show only used"
       # unchecked, every other variable the project uses follows them.
       # Only these feed Process, readiness and the cached-run signature.
       # None means nothing is rendered for the current slot.
       self._used_variables = None

       self.function_slots = {n: new_slot() for n in range(1, MAX_FUNCTION_SLOTS + 1)}
       self.current_slot = 1
       # Which slot's values the simulation fields are currently showing,
       # or None while they are blank. See _sim_fields_shown.
       self._sim_fields_slot = None
       self.last_rendered_slot = None
       self.last_rendered_function_text = None
       # The subfunctions as they were at that render. Editing one changes
       # the solved equation as much as editing the function does.
       self.last_rendered_subfunctions = None

       self.master_var_data = {}
       self.master_output_data = {}

       self.render_style = DEFAULT_RENDER_STYLE
       self.sensitivity_method = SENSITIVITY_METHODS[0]
       self.report_decimals = DEFAULT_REPORT_DECIMALS
       self.chart_style = DEFAULT_CHART_STYLE

       self._last_report_data = None
       # Signature of the inputs that produced _last_report_data. The
       # Results and Allocate Tol buttons stay live only while this still
       # matches the inputs on screen.
       self._results_signature = None

       # The annotator document lives for the life of the window so its
       # sheets survive closing the dialog and can always be serialized.
       self.annotator = AnnotatorWidget()
       self.annotator_dialog = None

       self._build_ui()
       self._refresh_slot_appearance()

   # ------------------------------------------------------------------
   # Mini inline LaTeX preview helper (Function row + each Subfunction row)
   # ------------------------------------------------------------------
   def _create_mini_latex_canvas(self):
       preview = LatexPreview(
           height=MINI_LATEX_HEIGHT,
           max_font_size=MINI_LATEX_FONT_SIZE,
           min_font_size=MINI_LATEX_MIN_FONT_SIZE,
       )
       preview.setMinimumWidth(MINI_LATEX_MIN_WIDTH)
       preview.setMaximumWidth(MINI_LATEX_MAX_WIDTH)
       preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
       return preview

   def _update_mini_latex(self, canvas, latex_str):
       canvas.set_latex(latex_str)
       # A new equation may need a wider or narrower preview column.
       self._schedule_column_layout()

   # ------------------------------------------------------------------
   # UI construction
   # ------------------------------------------------------------------
   def _build_ui(self):
       central = QWidget()
       self.setCentralWidget(central)
       main_layout = QVBoxLayout(central)

       # --- Menu bar background: match the app's default light gray
       #     across the full row, not just the corner widget. ---
       self.menuBar().setNativeMenuBar(False)
       self.menuBar().setStyleSheet("QMenuBar { background-color: #f0f0f0; }")

       # --- Corner widget: Settings gear + Help icon, both small flat
       #     icon buttons, placed in the menu bar's top-right corner. ---
       help_container = QWidget()
       help_container.setStyleSheet("background-color: #f0f0f0;")
       help_layout = QHBoxLayout(help_container)
       help_layout.setContentsMargins(10, 3, 10, 3)

       # Symbols, in order: framed picture, gear, question mark.
       self.annotate_btn = icon_button(
           "\U0001F5BC", "Annotations / Image Sheets", self.show_annotator
       )
       self.settings_btn = icon_button("\u2699", "Settings", self.show_settings)
       self.help_btn = icon_button("\u2753", "Help", self.show_help)

       help_layout.addWidget(self.annotate_btn)
       help_layout.addWidget(self.settings_btn)
       help_layout.addWidget(self.help_btn)
       self.menuBar().setCornerWidget(help_container, Qt.TopRightCorner)

       # --- Function row: label, slot #, entry field, desc, preview, [+] ---
       function_row_layout = QHBoxLayout()
       input_label = bold_label("Function:")

       self.slot_combo = NoScrollComboBox()
       for n in range(1, MAX_FUNCTION_SLOTS + 1):
           self.slot_combo.addItem(str(n))
       self.slot_combo.setCurrentIndex(0)  # slot 1 default
       self.slot_combo.setFixedWidth(60)
       self.slot_combo.currentIndexChanged.connect(self._on_slot_changed)

       self.function_input = QLineEdit()
       self.function_input.setPlaceholderText(
           "e.g.  P = P_b + (Q^2 (8*rho_d))/(C_d^2*d_o^4*pi^2)"
       )
       self.function_input.returnPressed.connect(self.process_function)
       self.function_input.textChanged.connect(lambda _: self._update_process_readiness())

       self.function_desc_input = line_edit(
           placeholder="Function Desc (optional)", width=DESC_FIELD_WIDTH
       )

       self.function_latex_canvas = self._create_mini_latex_canvas()

       self.add_subfunction_btn = icon_button(
           "+", "Add Subfunction", lambda: self.add_subfunction_row(),
           flat=False
       )

       self.function_row_layout = function_row_layout
       function_row_layout.addWidget(input_label)
       function_row_layout.addWidget(self.slot_combo)
       function_row_layout.addWidget(self.function_input, stretch=1)
       function_row_layout.addWidget(self.function_desc_input)
       # The preview and desc widths are set by _layout_function_columns;
       # the entry field stretches into whatever they leave.
       function_row_layout.addWidget(self.function_latex_canvas)
       function_row_layout.addWidget(self.add_subfunction_btn)
       main_layout.addLayout(function_row_layout)
       self._function_row_label = input_label
       self.function_desc_input.textChanged.connect(lambda _: self._schedule_column_layout())

       # --- Subfunctions container ------------------------------------------
       # Held in a scroll area whose height is recomputed from the row count
       # and capped at SUBFUNCTION_VISIBLE_ROWS. Without the cap the list
       # grows without limit and steals vertical space from the input
       # variables table, which is the part that actually needs the room.
       self.subfunctions_container = QWidget()
       self.subfunctions_layout = QVBoxLayout(self.subfunctions_container)
       self.subfunctions_layout.setContentsMargins(SUBFUNCTION_INDENT, 0, 0, 0)
       self.subfunctions_layout.setSpacing(4)

       self.subfunctions_scroll = QScrollArea()
       self.subfunctions_scroll.setWidget(self.subfunctions_container)
       self.subfunctions_scroll.setWidgetResizable(True)
       self.subfunctions_scroll.setFrameShape(QFrame.NoFrame)
       self.subfunctions_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
       self.subfunctions_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
       main_layout.addWidget(self.subfunctions_scroll)
       self._update_subfunctions_height()

       # --- Action row: Show only used and Render / Load Table, floated
       #     right. Save / Load live in the menu bar's left corner. ---
       function_actions_layout = QHBoxLayout()

       self.save_btn = text_button("Save Inputs", self.save_inputs)
       self.load_btn = text_button("Load Inputs", self.load_inputs)
       self.render_btn = text_button("Render / Load Table",
                                     self.process_function)

       self.show_only_used_checkbox = QCheckBox("Show only used")
       self.show_only_used_checkbox.setToolTip(
           "Hide subfunction rows that the rendered main function does not "
           "use. Hidden rows are only hidden \u2014 they are still saved and "
           "still validated."
       )
       self.show_only_used_checkbox.toggled.connect(
           lambda _: self._apply_subfunction_visibility()
       )

       # Name of the project file last loaded or saved; blank until then.
       # The full path is in the tooltip.
       self.project_file_label = QLabel("")
       self.project_file_label.setStyleSheet("color: gray;")

       # Save / Load and the open file's name sit at the left of the top
       # row, opposite the annotation / settings / help icons on the right.
       file_container = QWidget()
       file_container.setStyleSheet("background-color: #f0f0f0;")
       file_layout = QHBoxLayout(file_container)
       file_layout.setContentsMargins(10, 0, 10, 0)
       compact_font = QFont()
       compact_font.setPointSize(MENU_BAR_FONT_POINT_SIZE)
       for button in (self.save_btn, self.load_btn):
           button.setFont(compact_font)
           button.setFixedHeight(MENU_BAR_BUTTON_HEIGHT)
           button.setStyleSheet("QPushButton { padding: 0px 8px; }")
           file_layout.addWidget(button, alignment=Qt.AlignVCenter)
       self.project_file_label.setFont(compact_font)
       file_layout.addWidget(self.project_file_label, alignment=Qt.AlignVCenter)
       self._file_corner = file_container
       self._fit_file_corner()

       function_actions_layout.addStretch(1)
       function_actions_layout.addWidget(self.show_only_used_checkbox)
       function_actions_layout.addWidget(self.render_btn)
       main_layout.addLayout(function_actions_layout)

       # --- Final solved LaTeX display area ---------------------------------
       latex_label = bold_label("Rendered Expression (solved):")
       main_layout.addWidget(latex_label)

       # Same fit-then-scroll ladder as the inline previews, just taller
       # and with a larger font range. A 30-term stack-up overruns even the
       # full window width at 18pt, so this needs the scrollbar too.
       self.expr_preview = LatexPreview(
           height=LATEX_CANVAS_MAX_HEIGHT,
           max_font_size=LATEX_FONT_SIZE,
           min_font_size=LATEX_MIN_FONT_SIZE,
           centered=True,
       )
       self.expr_preview.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
       main_layout.addWidget(self.expr_preview)
       self._render_latex("")

       # --- Output variable table (the LHS, e.g. "P") ---------------------
       output_label = bold_label("Output Variable:")
       main_layout.addWidget(output_label)

       self.output_table = QTableWidget(0, len(OUTPUT_HEADERS))
       self.output_table.setHorizontalHeaderLabels(OUTPUT_HEADERS)
       self.output_table.verticalHeader().setVisible(False)
       self.output_table.setMaximumHeight(80)
       self._configure_column_widths(
           self.output_table, len(OUTPUT_HEADERS),
           OUT_NARROW_COL_WIDTHS, OUT_WIDE_COLS
       )
       main_layout.addWidget(self.output_table)

       # --- Input variables table -------------------------------------------
       table_label = bold_label("Input Variables:")
       self.show_only_used_vars_checkbox = QCheckBox("Show only used")
       self.show_only_used_vars_checkbox.setChecked(True)
       self.show_only_used_vars_checkbox.setToolTip(
           "Uncheck to also list every input variable used by any other "
           "function slot or subfunction. Those rows are shown in red "
           "after the ones the displayed function uses; they can be "
           "edited and are saved, but they don't affect Process."
       )
       self.show_only_used_vars_checkbox.toggled.connect(
           lambda _: self._refresh_variable_table()
       )
       table_header = QHBoxLayout()
       table_header.addWidget(table_label)
       table_header.addStretch(1)
       table_header.addWidget(self.show_only_used_vars_checkbox)
       main_layout.addLayout(table_header)

       self.table = QTableWidget(0, len(TABLE_HEADERS))
       self.table.setHorizontalHeaderLabels(TABLE_HEADERS)
       self.table.verticalHeader().setVisible(False)
       self._configure_column_widths(
           self.table, len(TABLE_HEADERS),
           NARROW_COL_WIDTHS, WIDE_COLS
       )
       main_layout.addWidget(self.table, stretch=1)

       # --- Simulation settings row: Iterations, Seed, Sensitivity Method,
       #     Eval Min/Max, then Process floated right (labels 1pt smaller) ---
       sim_layout = QHBoxLayout()

       # Every field here feeds a Process run, so all four invalidate the
       # cached results through the same readiness funnel.
       on_edit = lambda: self._update_process_readiness()

       sim_label = bold_label("Iterations:", SIM_LABEL_POINT_SIZE)
       self.iterations_input = line_edit(
           placeholder=DEFAULT_ITERATIONS,
           validator=QIntValidator(1, 100_000_000),
           width=COUNT_FIELD_WIDTH, on_change=on_edit
       )

       seed_label = bold_label("Seed:", SIM_LABEL_POINT_SIZE)
       self.seed_input = line_edit(
           placeholder="optional", validator=QIntValidator(0, 2_147_483_647),
           width=COUNT_FIELD_WIDTH, on_change=on_edit
       )

       eval_min_label = bold_label("Eval Min:", SIM_LABEL_POINT_SIZE)
       self.eval_min_input = line_edit(
           placeholder="optional", validator=double_validator(),
           width=LIMIT_FIELD_WIDTH, on_change=on_edit
       )

       eval_max_label = bold_label("Eval Max:", SIM_LABEL_POINT_SIZE)
       self.eval_max_input = line_edit(
           placeholder="optional", validator=double_validator(),
           width=LIMIT_FIELD_WIDTH, on_change=on_edit
       )

       self.process_btn = text_button("Process", self._on_process_clicked)
       self.process_all_btn = text_button(
           "Process All", self._on_process_all_clicked,
           tooltip="Run every function slot that has a formula, and show "
                   "one summary table. No charts or sensitivity analysis."
       )

       # Both of these replay the last Process run instead of re-sampling,
       # so they are live only while that run still matches the inputs on
       # screen. _update_results_availability owns their enabled state.
       self.allocate_btn = text_button(
           "Allocate Tol...", self.show_tolerance_allocation,
           tooltip="Work backwards from a required output spec to the "
                   "tolerance each variable can have. Requires a Process "
                   "run that matches the current inputs."
       )
       self.results_btn = text_button(
           "Results", self._on_results_clicked,
           tooltip="Reopen the report from the last Process run without "
                   "re-running it. Grays out as soon as any input feeding "
                   "that run changes."
       )
       self.allocate_btn.setEnabled(False)
       self.results_btn.setEnabled(False)

       sim_layout.addWidget(sim_label)
       sim_layout.addWidget(self.iterations_input)
       sim_layout.addSpacing(12)
       sim_layout.addWidget(seed_label)
       sim_layout.addWidget(self.seed_input)
       sim_layout.addSpacing(16)
       sim_layout.addSpacing(16)
       sim_layout.addWidget(eval_min_label)
       sim_layout.addWidget(self.eval_min_input)
       sim_layout.addWidget(eval_max_label)
       sim_layout.addWidget(self.eval_max_input)
       sim_layout.addStretch(1)
       sim_layout.addWidget(self.process_btn)
       sim_layout.addWidget(self.process_all_btn)
       sim_layout.addWidget(self.allocate_btn)
       sim_layout.addWidget(self.results_btn)
       main_layout.addLayout(sim_layout)

       self._process_ready = False
       self._process_reasons = []

   # ------------------------------------------------------------------
   # Subfunction row management
   #
   # Size the subfunction scroll area to its rows, up to the cap. Measured
   # from the rows themselves rather than a hardcoded pixel height so it
   # stays correct under a different font, DPI, or platform style.
   # ------------------------------------------------------------------
   def _update_subfunctions_height(self):
       # isVisibleTo, not isVisible/isHidden: a row added moments ago is
       # still flagged hidden until the event loop shows it, which would
       # size the area one row behind. isVisibleTo asks only whether the
       # row was explicitly hidden -- which is what the "show only used"
       # filter does and the only case meant to be skipped here.
       rows = [e for e in self.subfunction_rows
               if e["container"].isVisibleTo(self.subfunctions_container)]
       if not rows:
           self.subfunctions_scroll.setFixedHeight(0)
           self._align_function_row_with_subfunctions(0)
           self._schedule_column_layout()
           return

       row_height = max(e["container"].sizeHint().height() for e in rows)
       shown = min(len(rows), SUBFUNCTION_VISIBLE_ROWS)
       margins = self.subfunctions_layout.contentsMargins()
       height = (
           shown * row_height
           + (shown - 1) * self.subfunctions_layout.spacing()
           + margins.top() + margins.bottom()
       )
       self.subfunctions_scroll.setFixedHeight(height)
       self._align_function_row_with_subfunctions(len(rows))
       self._schedule_column_layout()

   def resizeEvent(self, event):
       super().resizeEvent(event)
       self._schedule_column_layout()

   def _schedule_column_layout(self):
       """Run _layout_function_columns once, after the current event --
       a render updates every preview in turn, and Qt must first lay the
       window out at its new size after a resize."""
       if not hasattr(self, "_column_layout_timer"):
           self._column_layout_timer = QTimer(self)
           self._column_layout_timer.setSingleShot(True)
           self._column_layout_timer.timeout.connect(self._layout_function_columns)
       self._column_layout_timer.start(0)

   def _layout_function_columns(self):
       """Size the preview and description columns of the function and
       subfunction rows, shared by every row so they stay aligned.

       Previews: the narrowest width that shows the widest equation at
       full size, capped at what each used to get -- an even split of the
       spare width with the entry field -- so a long equation behaves as
       before. Descriptions: widened together to fit the longest one, but
       only by the width the previews freed, and never below
       DESC_FIELD_WIDTH. The entry fields stretch into the rest.
       """
       row_width = self.function_row_layout.contentsRect().width()
       if row_width <= 0:
           return   # not laid out yet; resizeEvent calls back
       spacing = self.function_row_layout.spacing()
       fixed = (self._function_row_label.sizeHint().width()
                + self.slot_combo.width() + self.add_subfunction_btn.width()
                + DESC_FIELD_WIDTH + 5 * spacing)
       cap = max(MINI_PREVIEW_MIN_WIDTH, (row_width - fixed) // 2)

       visible = [e for e in self.subfunction_rows
                  if e["container"].isVisibleTo(self.subfunctions_container)]
       previews = [self.function_latex_canvas] + [e["preview_canvas"] for e in visible]
       natural = max(p.natural_width() for p in previews)
       preview_width = min(cap, max(natural, MINI_PREVIEW_MIN_WIDTH))

       descs = [self.function_desc_input] + [e["desc_edit"] for e in visible]
       desc_needed = max(line_edit_text_width(d, d.text() or d.placeholderText())
                         for d in descs)
       freed = cap - preview_width
       desc_width = DESC_FIELD_WIDTH + min(freed, max(0, desc_needed - DESC_FIELD_WIDTH))

       # Hidden rows too, so a row shown later already matches.
       for preview in [self.function_latex_canvas] + [e["preview_canvas"] for e in self.subfunction_rows]:
           preview.setFixedWidth(preview_width)
       for desc in [self.function_desc_input] + [e["desc_edit"] for e in self.subfunction_rows]:
           desc.setFixedWidth(desc_width)
       # Apply the new widths now rather than on Qt's queued layout pass,
       # so the rows are never shown for a moment at mismatched positions.
       # The subfunction rows sit in their own scroll area, each with its
       # own layout, so they are activated too.
       layout = self.centralWidget().layout() if self.centralWidget() else None
       if layout is not None:
           layout.activate()
       self.subfunctions_layout.activate()
       for entry in self.subfunction_rows:
           entry["container"].layout().activate()

   def _align_function_row_with_subfunctions(self, visible_rows):
       """When the subfunction list scrolls, its scrollbar narrows those
       rows; give the function row the same right margin so its columns
       still line up with theirs."""
       scrolls = visible_rows > SUBFUNCTION_VISIBLE_ROWS
       right = (self.subfunctions_scroll.verticalScrollBar().sizeHint().width()
                if scrolls else 0)
       self.function_row_layout.setContentsMargins(0, 0, right, 0)

   def add_subfunction_row(self, text="", desc=""):
       row_widget = QWidget()
       row_layout = QHBoxLayout(row_widget)
       row_layout.setContentsMargins(0, 0, 0, 0)

       label = QLabel("Sub:")
       edit = line_edit(text, placeholder="e.g. rho_d = A_rho*T + B_rho")
       desc_edit = line_edit(desc, placeholder="Function Desc (optional)",
                             width=DESC_FIELD_WIDTH)
       preview_canvas = self._create_mini_latex_canvas()
       # Minus sign; connected below, once the entry it removes exists.
       remove_btn = icon_button("\u2212", "Remove this subfunction",
                                flat=False)

       # Preview and desc widths are shared with the function row; see
       # _layout_function_columns.
       desc_edit.textChanged.connect(lambda _: self._schedule_column_layout())
       row_layout.addWidget(label)
       row_layout.addWidget(edit, stretch=1)
       row_layout.addWidget(desc_edit)
       row_layout.addWidget(preview_canvas)
       row_layout.addWidget(remove_btn)

       self.subfunctions_layout.addWidget(row_widget)
       # Qt leaves a newly added child flagged hidden until the parent's next
       # layout pass. The row is meant to be visible the moment it exists, and
       # showing it now also lets _update_subfunctions_height count it.
       row_widget.show()

       entry = {
           "container": row_widget, "edit": edit,
           "desc_edit": desc_edit, "preview_canvas": preview_canvas
       }
       remove_btn.clicked.connect(lambda: self.remove_subfunction_row(entry))
       edit.textChanged.connect(lambda _: self._update_process_readiness())
       self.subfunction_rows.append(entry)
       self._update_subfunctions_height()
       self._update_process_readiness()
       return entry

   def remove_subfunction_row(self, entry):
       entry["container"].setParent(None)
       if entry in self.subfunction_rows:
           self.subfunction_rows.remove(entry)
       self._update_subfunctions_height()
       self._update_process_readiness()

   def clear_subfunction_rows(self):
       for entry in list(self.subfunction_rows):
           self.remove_subfunction_row(entry)

   # ------------------------------------------------------------------
   # Drop rows the user added but never filled in. Called at render time,
   # so a row added by mistake clears itself on the next Render instead of
   # occupying space and a scroll position forever.
   #
   # A row counts as empty only when BOTH the function and its description
   # are blank. A description on its own is unfinished work, not clutter,
   # and rendering must never delete something the user typed.
   # ------------------------------------------------------------------
   def _prune_empty_subfunction_rows(self):
       for entry in list(self.subfunction_rows):
           if (not entry["edit"].text().strip()
                   and not entry["desc_edit"].text().strip()):
               self.remove_subfunction_row(entry)

   # ------------------------------------------------------------------
   # "Show only used" filter — purely a display filter. Hidden rows stay
   # in self.subfunction_rows, so they are still parsed, still validated,
   # and still written out by save_inputs.
   # ------------------------------------------------------------------
   def _apply_subfunction_visibility(self):
       show_all = (
           not self.show_only_used_checkbox.isChecked()
           or self._used_subfunction_names is None
       )
       for entry in self.subfunction_rows:
           if show_all:
               entry["container"].setVisible(True)
               continue
           lhs_name = lhs_name_of(entry["edit"].text())
           # A blank or unparseable row has no name to match on, so keep
           # it visible — otherwise a newly added row would vanish before
           # the user could type in it.
           visible = lhs_name is None or lhs_name in self._used_subfunction_names
           entry["container"].setVisible(visible)

       # Hiding rows changes how many are on screen, so the cap is re-applied.
       self._update_subfunctions_height()

   # ------------------------------------------------------------------
   # Name-collision validation, run once at render time.
   #
   # Rules:
   #   * Two subfunctions may not define the same name.
   #   * A subfunction name may not collide with any main function's
   #     output name (in any slot, not just the current one).
   #   * Main functions may freely share output names with each other.
   # ------------------------------------------------------------------
   def _validate_name_collisions(self):
       errors = []

       sub_names_seen = {}
       sub_names = set()
       for idx, entry in enumerate(self.subfunction_rows, start=1):
           sub_text = entry["edit"].text().strip()
           if not sub_text:
               continue
           name = lhs_name_of(sub_text)
           if name is None:
               continue  # malformed rows are reported by the parser later
           if name in sub_names_seen:
               errors.append(
                   f"Subfunction '{name}' is defined more than once "
                   f"(row {sub_names_seen[name]} and row {idx}). "
                   f"Rename one of them or remove the duplicate."
               )
           else:
               sub_names_seen[name] = idx
               sub_names.add(name)

       slot_texts = {}
       for n, slot in self.function_slots.items():
           text = slot["text"].strip()
           if text:
               slot_texts[n] = text
       # The current slot's on-screen text may not be committed to
       # function_slots yet, so it takes precedence over the stored copy.
       current_text = self.function_input.text().strip()
       if current_text:
           slot_texts[self.current_slot] = current_text
       else:
           slot_texts.pop(self.current_slot, None)

       for n, text in sorted(slot_texts.items()):
           name = lhs_name_of(text)
           if name is not None and name in sub_names:
               errors.append(
                   f"Function slot {n} outputs '{name}', but a subfunction "
                   f"already defines '{name}'. Rename one of them."
               )

       return errors

   # ------------------------------------------------------------------
   # Column width helpers
   # ------------------------------------------------------------------
   def _fit_var_column(self, table_widget, col):
       """Widen a Var column to its longest name, never below the minimum,
       so a long variable name is shown in full instead of as 't_co...'."""
       metrics = table_widget.fontMetrics()
       widest = 0
       for row in range(table_widget.rowCount()):
           item = table_widget.item(row, col)
           if item is not None:
               widest = max(widest, metrics.horizontalAdvance(item.text()))
       table_widget.setColumnWidth(col, max(VAR_COL_MIN_WIDTH, widest + VAR_COL_PADDING))

   def _fit_input_columns(self):
       """Size each fixed input-table column to the widest of its header
       and cells -- as narrow as possible, never wider than its maximum in
       NARROW_COL_WIDTHS (Var has none). An empty entry field is sized to
       its placeholder, so "numeric" stays readable."""
       header_metrics = self.table.horizontalHeader().fontMetrics()
       metrics = self.table.fontMetrics()
       for col in INPUT_FIT_COLS:
           header = self.table.horizontalHeaderItem(col)
           width = header_metrics.horizontalAdvance(header.text() if header else "") + FIT_PADDING_HEADER
           for row in range(self.table.rowCount()):
               cell = self.table.cellWidget(row, col)
               if isinstance(cell, QLineEdit):
                   width = max(width, self._cell_width(cell, cell.text() or cell.placeholderText()))
               elif isinstance(cell, QComboBox):
                   width = max(width, self._cell_width(cell, cell.currentText()))
               else:
                   item = self.table.item(row, col)
                   if item is not None:
                       width = max(width, metrics.horizontalAdvance(item.text()) + VAR_COL_PADDING)
           if col != COL_VAR:
               width = min(width, self._max_column_width(col))
           self.table.setColumnWidth(col, width)

   def _cell_width(self, cell, text):
       """Width a cell widget needs to show `text` in full, as its own
       style computes it -- frame, margins and, for a drop-down, the
       arrow -- so the result is right for any platform and font."""
       style = cell.style()
       text_size = QSize(cell.fontMetrics().horizontalAdvance(text), cell.fontMetrics().height())
       if isinstance(cell, QComboBox):
           option = QStyleOptionComboBox()
           option.initFrom(cell)
           option.editable = cell.isEditable()
           return style.sizeFromContents(QStyle.CT_ComboBox, option, text_size, cell).width()
       return line_edit_text_width(cell, text)

   def _max_column_width(self, col):
       """A column's width limit from NARROW_COL_WIDTHS -- raised for a
       drop-down column to fit its longest option, which must never be
       cut off whatever the font."""
       limit = NARROW_COL_WIDTHS[col]
       for row in range(self.table.rowCount()):
           cell = self.table.cellWidget(row, col)
           if isinstance(cell, QComboBox):
               longest = max((self._cell_width(cell, cell.itemText(i)) for i in range(cell.count())), default=0)
               return max(limit, longest)
       return limit

   def _configure_column_widths(self, table_widget, n_cols, narrow_widths, wide_cols):
       header = table_widget.horizontalHeader()
       for col in range(n_cols):
           if col in wide_cols:
               header.setSectionResizeMode(col, QHeaderView.Stretch)
           else:
               header.setSectionResizeMode(col, QHeaderView.Fixed)
               table_widget.setColumnWidth(col, narrow_widths.get(col, 70))

   # ------------------------------------------------------------------
   # Help dialog
   # ------------------------------------------------------------------
   def show_help(self):
       dialog = QDialog(self)
       dialog.setWindowTitle("Function Syntax & Sensitivity Method Help")
       dialog.resize(700, 750)
       layout = QVBoxLayout(dialog)

       text = QTextEdit()
       text.setReadOnly(True)
       text.setHtml(HELP_HTML)
       layout.addWidget(text)

       # No Close button: the window's own [x] closes it, and this dialog
       # has nothing to confirm or cancel. Escape also works.
       dialog.exec_()

   # ------------------------------------------------------------------
   # Annotator window
   # ------------------------------------------------------------------
   def show_annotator(self):
       if self.annotator_dialog is None:
           self.annotator_dialog = AnnotatorDialog(self.annotator, self)
       self.annotator_dialog.show()
       self.annotator_dialog.raise_()
       self.annotator_dialog.activateWindow()
       self.annotator.fit_current()

   # ------------------------------------------------------------------
   # Tolerance allocation dialog
   # ------------------------------------------------------------------
   def show_tolerance_allocation(self):
       # The button is disabled unless this holds; the check is here so a
       # stale run can never be reached by another code path.
       if not self._results_available():
           return
       report = self._last_report_data

       dialog = QDialog(self)
       dialog.setWindowTitle("Tolerance Allocation")
       dialog.resize(860, 560)
       layout = QVBoxLayout(dialog)

       unit = report["output_unit_str"]
       baseline = report["oat_baseline"]

       intro = QLabel(
           f"Working backwards from a required output spec to the tolerance "
           f"each variable can carry.\nAll-nominal output: "
           f"<b>{baseline:.6g} {unit}</b>"
       )
       intro.setTextFormat(Qt.RichText)
       layout.addWidget(intro)

       form = QHBoxLayout()
       # Seeded from the Eval limits when they exist, since that is where
       # the user already stated the output requirement.
       def target_field(value):
           return line_edit("" if value is None else format_number(value),
                            validator=double_validator(),
                            width=TARGET_FIELD_WIDTH)

       min_input = target_field(report["eval_min"])
       max_input = target_field(report["eval_max"])
       cpk_input = target_field(DEFAULT_TARGET_CPK)

       form.addWidget(QLabel("Target Min:"))
       form.addWidget(min_input)
       form.addWidget(QLabel("Target Max:"))
       form.addWidget(max_input)
       form.addWidget(QLabel("Target Cpk:"))
       form.addWidget(cpk_input)
       calc_btn = QPushButton("Calculate")   # connected once _calculate exists
       form.addWidget(calc_btn)
       form.addStretch(1)
       layout.addLayout(form)

       summary = QLabel("Enter a target spec and click Calculate.")
       summary.setWordWrap(True)
       layout.addWidget(summary)

       headers = ["Variable", "Cp", "Current \u00b1Tol", "% of Variance",
                  "Uniform \u00b1Tol", "Equal-Contrib \u00b1Tol"]
       table = QTableWidget(0, len(headers))
       table.setHorizontalHeaderLabels(headers)
       table.horizontalHeader().setStretchLastSection(True)
       table.setEditTriggers(QTableWidget.NoEditTriggers)
       layout.addWidget(table)

       note = note_label(
           "<b>Uniform</b> scales every tolerance by one factor, keeping the "
           "scheme you already chose. <b>Equal-Contribution</b> gives each "
           "variable the same share of the variance budget, tightening the "
           "sensitive ones and loosening the rest \u2014 usually cheaper to "
           "buy, but it redistributes rather than scales.<br>"
           "Both assume each variable's output contribution is proportional "
           "to its tolerance, which is the same linear model behind the RSS "
           "estimate. Treat the result as a starting point and re-run "
           "Process with the new tolerances to confirm."
       )
       layout.addWidget(note)

       close_row = QHBoxLayout()
       close_row.addStretch(1)
       close_row.addWidget(text_button("Close", dialog.accept))
       layout.addLayout(close_row)

       def _calculate():
           try:
               if not min_input.text().strip() or not max_input.text().strip():
                   raise ValueError("Enter both a Target Min and a Target Max.")
               result = self._allocate_tolerances(
                   report["var_info"], report["oat_results"], baseline,
                   float(min_input.text()), float(max_input.text()),
                   float(cpk_input.text() or DEFAULT_TARGET_CPK),
               )
           except Exception as exc:
               table.setRowCount(0)
               summary.setText(f"<span style='color:#b00'>{exc}</span>")
               summary.setTextFormat(Qt.RichText)
               return

           summary.setTextFormat(Qt.RichText)
           summary.setText(
               f"Nearest-limit margin: <b>{result['margin']:.6g} {unit}</b> "
               f"&nbsp;|&nbsp; allowed \u03c3 at Cpk "
               f"{format_number(result['target_cpk'])}: "
               f"<b>{result['allowed_sigma']:.6g} {unit}</b><br>"
               f"Current RSS \u03c3: <b>{result['current_sigma']:.6g} {unit}</b> "
               f"(Cpk \u2248 <b>{result['current_cpk']:.3f}</b>) "
               f"&nbsp;|&nbsp; every tolerance must scale by "
               f"<b>{result['uniform_factor']:.3f}\u00d7</b>"
           )

           table.setRowCount(len(result["rows"]))
           for i, r in enumerate(result["rows"]):
               cells = [
                   r["var"],
                   format_number(r["cp"]),
                   f"\u00b1{r['half_tol']:.6g}",
                   f"{r['share_now']:.1f}%",
                   f"\u00b1{r['uniform_half_tol']:.6g}",
                   f"\u00b1{r['equal_half_tol']:.6g}  ({r['equal_factor']:.2f}\u00d7)",
               ]
               for col, value in enumerate(cells):
                   table.setItem(i, col, QTableWidgetItem(value))
           table.resizeColumnsToContents()

       calc_btn.clicked.connect(_calculate)
       for widget in (min_input, max_input, cpk_input):
           widget.returnPressed.connect(_calculate)
       if report["eval_min"] is not None and report["eval_max"] is not None:
           _calculate()   # spec already known, so show the answer immediately

       dialog.exec_()

   # ------------------------------------------------------------------
   # Settings dialog
   # ------------------------------------------------------------------
   def show_settings(self):
       dialog = QDialog(self)
       dialog.setWindowTitle("Settings")
       dialog.resize(420, 400)
       layout = QVBoxLayout(dialog)

       # Three identical groups: a heading, a combo seeded from the
       # current value, and a gray note underneath.
       def setting_group(heading, items, current, note):
           layout.addWidget(bold_label(heading))
           combo = choice_box(items, current)
           layout.addWidget(combo)
           layout.addWidget(note_label(note))
           return combo

       style_combo = setting_group(
           "Rendered Expression Style:", RENDER_STYLES, self.render_style,
           "Changes how the solved equation is displayed. Does not affect "
           "the Monte Carlo calculation itself. Some styles (Factored, "
           "Partial Fractions) mainly apply to single-variable rational "
           "expressions and may have no visible effect on complex "
           "multi-variable equations."
       )

       chart_combo = setting_group(
           "Monte Carlo Distribution Chart:", DISTRIBUTION_CHART_STYLES,
           self.chart_style,
           "Controls how the output distribution is plotted after "
           "Process runs. Takes effect on the next Process click."
       )

       method_combo = setting_group(
           "Sensitivity Analysis Method:", SENSITIVITY_METHODS,
           self.sensitivity_method,
           "Which method ranks the input variables in the sensitivity "
           "chart. Does not change the Monte Carlo results themselves. "
           "Takes effect on the next Process click."
       )

       layout.addWidget(bold_label("Report Decimal Places (max):"))
       decimals_spin = QSpinBox()
       decimals_spin.setRange(0, MAX_REPORT_DECIMALS)
       decimals_spin.setValue(self.report_decimals)
       layout.addWidget(decimals_spin)
       layout.addWidget(note_label(
           "Numbers in both reports, on screen and in the PDF, are rounded "
           "to at most this many decimal places. Values with fewer decimals "
           "are shown as they are. Takes effect on the next Process or "
           "Process All click."
       ))

       btn_row = QHBoxLayout()
       apply_btn = QPushButton("Apply")   # connected once _apply exists
       btn_row.addStretch(1)
       btn_row.addWidget(apply_btn)
       btn_row.addWidget(text_button("Close", dialog.accept))
       layout.addLayout(btn_row)

       def _apply():
           self.render_style = style_combo.currentText()
           self.chart_style = chart_combo.currentText()
           self.sensitivity_method = method_combo.currentText()
           self.report_decimals = decimals_spin.value()
           # The sensitivity method is part of the report, so switching it
           # invalidates the cached run.
           self._update_process_readiness()
           if self.solved_expr is not None:
               self._refresh_solved_display()

       apply_btn.clicked.connect(_apply)

       dialog.exec_()

   # ------------------------------------------------------------------
   # Re-render the big solved-expression display using the current
   # render style, without re-parsing or touching the variable table.
   # ------------------------------------------------------------------
   def _refresh_solved_display(self):
       if self.solved_expr is None:
           return
       lhs_name = lhs_name_of(self.function_input.text())
       styled_latex = display_latex(
           apply_render_style(self.solved_expr, self.render_style)
       )
       self._render_latex(
           f"{display_latex(sympy.Symbol(lhs_name))} = {styled_latex}"
           if lhs_name else styled_latex
       )

   # ------------------------------------------------------------------
   # Slot dropdown appearance: bold + normal color if a formula exists
   # in that slot, gray + normal weight if empty.
   # ------------------------------------------------------------------
   def _refresh_slot_appearance(self):
       bold_font = QFont()
       bold_font.setBold(True)
       normal_font = QFont()

       for i in range(self.slot_combo.count()):
           n = i + 1
           has_text = bool(self.function_slots[n]["text"].strip())
           if has_text:
               self.slot_combo.setItemData(i, bold_font, Qt.FontRole)
               self.slot_combo.setItemData(i, QColor("black"), Qt.ForegroundRole)
           else:
               self.slot_combo.setItemData(i, normal_font, Qt.FontRole)
               self.slot_combo.setItemData(i, QColor("gray"), Qt.ForegroundRole)

   # ------------------------------------------------------------------
   # Handle switching function slots
   # ------------------------------------------------------------------
   def _on_slot_changed(self, index):
       new_index = index + 1

       self._sync_master_var_data()
       self._sync_master_output_data()

       self._capture_current_slot()
       self._refresh_slot_appearance()

       self.current_slot = new_index
       slot_data = self.function_slots[new_index]
       self.function_input.setText(slot_data["text"])
       self.function_desc_input.setText(slot_data["desc"])
       self._update_mini_latex(self.function_latex_canvas, "")

       # The incoming slot's simulation settings are its own, and they
       # arrive with Render along with its variables and output unit.
       # Leaving the previous slot's numbers on screen in the meantime
       # would invite a Process against the wrong Eval limits.
       self._clear_sim_fields()

       self._used_variables = None
       self.table.setRowCount(0)
       self.output_table.setRowCount(0)
       self._render_latex("")
       self.solved_expr = None

       # The previous slot's usage result says nothing about this slot,
       # so fall back to showing every subfunction until the next render.
       self._used_subfunction_names = None
       self._apply_subfunction_visibility()

       # A slot that already has a function is rendered straight away, so
       # its table, output and simulation settings are on screen and
       # Process is ready. An empty slot is left blank for a new function.
       if slot_data["text"].strip():
           self.process_function()
       else:
           self._update_process_readiness()
           self._layout_function_columns()

   # ------------------------------------------------------------------
   # The simulation fields belong to the current slot
   #
   # They are cleared on a slot switch and filled in by Render, so at any
   # moment they either show one slot's values or show nothing. Which of
   # those is tracked outright, in _sim_fields_slot, rather than inferred
   # from last_rendered_slot: that one is not reset when you switch away,
   # so on returning to a slot you had already rendered it would claim the
   # freshly blanked fields were that slot's values and write the blanks
   # back over them.
   # ------------------------------------------------------------------
   def _sim_widgets(self):
       return {
           "iterations": self.iterations_input,
           "seed": self.seed_input,
           "eval_min": self.eval_min_input,
           "eval_max": self.eval_max_input,
       }

   def _sim_fields_shown(self):
       return self._sim_fields_slot == self.current_slot

   def _clear_sim_fields(self):
       for widget in self._sim_widgets().values():
           widget.clear()
       self._sim_fields_slot = None

   def _load_sim_fields(self):
       """Fill the fields from the current slot."""
       slot = self.function_slots[self.current_slot]
       for key, widget in self._sim_widgets().items():
           widget.setText(slot.get(key, ""))
       self._sim_fields_slot = self.current_slot

   def _capture_current_slot(self):
       """Merge what is on screen into the current slot's entry.

       The function text and description are always current. The
       simulation fields are only written back when they are actually
       showing this slot, so switching away from a slot that was never
       rendered leaves its stored settings alone instead of blanking them.
       """
       slot = self.function_slots[self.current_slot]
       slot["text"] = self.function_input.text().strip()
       slot["desc"] = self.function_desc_input.text().strip()
       if self._sim_fields_shown():
           for key, widget in self._sim_widgets().items():
               slot[key] = widget.text()

   # ------------------------------------------------------------------
   # Master store sync — merges whatever is currently on-screen into the
   # persistent cross-function stores.
   # ------------------------------------------------------------------
   def _var_row(self, row):
       return VarRow(self.table, row)

   def _var_rows(self):
       return (VarRow(self.table, row) for row in range(self.table.rowCount()))

   def _is_used_var(self, var):
       return var in (self._used_variables or ())

   def _used_var_rows(self):
       """Rows of the variables the displayed function uses -- the only
       rows that feed Process. Extra rows shown by unchecking "Show only
       used" are editable and saved, but never part of a run."""
       return (row for row in self._var_rows() if self._is_used_var(row.var))

   def _table_variables(self):
       """Variables to list in the input table, used ones first."""
       used = list(self._used_variables or [])
       if self.show_only_used_vars_checkbox.isChecked():
           return used
       referenced_vars, _ = self._compute_referenced_names()
       # The referenced set also holds subfunction names, which are
       # intermediate results rather than input variables.
       sub_names = {
           lhs_name_of(entry["edit"].text()) for entry in self.subfunction_rows
       }
       others = sorted(referenced_vars - sub_names - set(used), key=str.lower)
       return used + others

   def _refresh_variable_table(self):
       self._update_table(self._table_variables())

   def _sync_master_var_data(self):
       for row in self._var_rows():
           if not row.var:
               continue
           self.master_var_data[row.var] = row.as_data()

   def _output_cell(self, col):
       if self.output_table.rowCount() != 1:
           return None
       return self.output_table.cellWidget(0, col)

   def _output_text(self, col):
       widget = self._output_cell(col)
       return widget.text() if widget is not None else ""

   def _output_var_name(self):
       if self.output_table.rowCount() != 1:
           return ""
       item = self.output_table.item(0, OUT_COL_VAR)
       return item.text() if item is not None else ""

   def _output_unit_text(self):
       return self._output_text(OUT_COL_UNIT)

   def _sync_master_output_data(self):
       var = self._output_var_name()
       if not var:
           return
       self.master_output_data[var] = {
           "name": self._output_text(OUT_COL_NAME),
           "unit": self._output_unit_text(),
       }

   # ------------------------------------------------------------------
   # Shared helper: what variable names / output names are referenced
   # by any non-empty function slot, plus every subfunction.
   # ------------------------------------------------------------------
   def _compute_referenced_names(self):
       referenced_vars = set()
       referenced_outputs = set()

       texts = [slot["text"] for slot in self.function_slots.values() if slot["text"].strip()]
       current_typed = self.function_input.text().strip()
       if current_typed:
           texts.append(current_typed)

       for text in texts:
           if "=" not in text:
               continue
           lhs_text, rhs_text = text.split("=", 1)
           lhs_name = lhs_name_of(text)
           if lhs_name is not None:
               referenced_outputs.add(lhs_name)
           try:
               expr = parse_side(rhs_text.strip())
               referenced_vars |= {str(s) for s in expr.free_symbols if str(s) not in RESERVED_NAMES}
           except Exception:
               pass

       for entry in self.subfunction_rows:
           sub_text = entry["edit"].text().strip()
           if "=" not in sub_text:
               continue
           lhs_text, rhs_text = sub_text.split("=", 1)
           try:
               rhs_expr = parse_side(rhs_text.strip())
               referenced_vars |= {str(s) for s in rhs_expr.free_symbols if str(s) not in RESERVED_NAMES}
           except Exception:
               pass

       return referenced_vars, referenced_outputs

   # ------------------------------------------------------------------
   # Expression parsing (main function + subfunctions, with substitution)
   # ------------------------------------------------------------------
   def process_function(self):
       raw_text = self.function_input.text().strip()
       if not raw_text:
           QMessageBox.warning(self, "Empty input", "Please enter a function.")
           return

       self._prune_empty_subfunction_rows()

       collision_errors = self._validate_name_collisions()
       if collision_errors:
           bullet_list = "\n\n".join(f"\u2022 {e}" for e in collision_errors)
           QMessageBox.critical(
               self, "Duplicate Name",
               f"This function cannot be rendered:\n\n{bullet_list}"
           )
           return

       try:
           lhs_name, main_expr = parse_main_function(raw_text)
       except MainFunctionParseError as exc:
           QMessageBox.critical(self, exc.title, exc.message)
           self._update_mini_latex(self.function_latex_canvas, r"\text{Error}")
           self._render_latex(r"\text{Error parsing main function}")
           return
       except CalculationError as exc:
           QMessageBox.critical(self, exc.title, exc.message)
           return

       lhs_latex = display_latex(sympy.Symbol(lhs_name)) if lhs_name else None
       main_latex_raw = display_latex(main_expr)
       main_line_raw = f"{lhs_latex} = {main_latex_raw}" if lhs_latex else main_latex_raw
       self._update_mini_latex(self.function_latex_canvas, main_line_raw)

       try:
           subfunc_map, parsed_rows = parse_subfunctions(
               [entry["edit"].text() for entry in self.subfunction_rows]
           )
       except SubfunctionParseError as exc:
           QMessageBox.critical(self, exc.title, exc.message)
           self._update_mini_latex(
               self.subfunction_rows[exc.row_index]["preview_canvas"], r"\text{Error}"
           )
           return

       for entry, parsed in zip(self.subfunction_rows, parsed_rows):
           if parsed is None:
               self._update_mini_latex(entry["preview_canvas"], "")
           else:
               sub_lhs_expr, sub_rhs_expr = parsed
               sub_line = f"{display_latex(sub_lhs_expr)} = {display_latex(sub_rhs_expr)}"
               self._update_mini_latex(entry["preview_canvas"], sub_line)

       try:
           check_subfunction_cycle(subfunc_map)
       except CalculationError as exc:
           QMessageBox.critical(self, exc.title, exc.message)
           return

       self._used_subfunction_names = used_subfunction_names(
           main_expr, subfunc_map
       )
       self._apply_subfunction_visibility()

       try:
           solved_expr, variables = solve_function(main_expr, subfunc_map)
       except CalculationError as exc:
           QMessageBox.critical(self, exc.title, exc.message)
           return

       self.solved_expr = solved_expr

       styled_expr = apply_render_style(solved_expr, self.render_style)
       solved_latex = display_latex(styled_expr)
       solved_line = f"{lhs_latex} = {solved_latex}" if lhs_latex else solved_latex
       self._render_latex(solved_line)

       self._update_output_table(lhs_name)
       self._used_variables = variables
       self._refresh_variable_table()

       # A gap between a Piecewise's conditions isn't an error -- the
       # formula renders and runs -- but samples landing in it come out
       # undefined, so say so now rather than leave it to a NaN popup.
       gaps = piecewise_gap_warnings(
           [(lhs_name or "Main function", main_expr)]
           + [(str(sym), rhs) for sym, rhs in subfunc_map.items()
              if str(sym) in self._used_subfunction_names]
       )
       if gaps:
           QMessageBox.warning(
               self, "Piecewise Gap",
               "The function rendered, but:\n\n"
               + "\n\n".join(f"\u2022 {g}" for g in gaps)
               + "\n\nEnd the Piecewise with a (formula, True) branch to "
                 "cover every remaining value."
           )

       # First render since arriving at this slot -> the blank fields are
       # filled from it. Any later render -> what is on screen is the
       # newer truth and was just written back by the capture above.
       self._capture_current_slot()
       if not self._sim_fields_shown():
           self._load_sim_fields()
       self._refresh_slot_appearance()

       self.last_rendered_slot = self.current_slot
       self.last_rendered_function_text = raw_text
       self.last_rendered_subfunctions = self._current_subfunction_texts()
       # Now, not on the deferred pass, so the new equations are never
       # drawn for a frame at the previous column widths.
       self._layout_function_columns()
       # Each subfunction's own text at this render, for its preview.
       for entry in self.subfunction_rows:
           entry["rendered_text"] = entry["edit"].text().strip()
       self._update_process_readiness()

   # ------------------------------------------------------------------
   # LaTeX rendering — single final solved equation
   # ------------------------------------------------------------------
   def _render_latex(self, latex_str):
       self.expr_preview.set_latex(latex_str)

   # ------------------------------------------------------------------
   # Output table (LHS variable) management
   # ------------------------------------------------------------------
   def _update_output_table(self, lhs_name):
       self._sync_master_output_data()

       self.output_table.setRowCount(0)
       if not lhs_name:
           self._update_process_readiness()
           return

       self.output_table.setRowCount(1)
       saved = self.master_output_data.get(lhs_name, {})

       var_item = QTableWidgetItem(lhs_name)
       var_item.setFlags(var_item.flags() & ~Qt.ItemIsEditable)
       var_item.setTextAlignment(Qt.AlignCenter)
       self.output_table.setItem(0, OUT_COL_VAR, var_item)
       self._fit_var_column(self.output_table, OUT_COL_VAR)

       name_edit = line_edit(saved.get("name", ""),
                             placeholder="descriptive name")
       self.output_table.setCellWidget(0, OUT_COL_NAME, name_edit)

       unit_edit = line_edit(saved.get("unit", ""),
                             placeholder=UNIT_PLACEHOLDER_HINT)
       self.output_table.setCellWidget(0, OUT_COL_UNIT, unit_edit)

       preview_label = unit_preview_label()
       self.output_table.setCellWidget(0, OUT_COL_UNIT_PREVIEW, preview_label)

       def _on_output_unit_changed():
           self._update_unit_preview(preview_label, unit_edit.text())
           self._update_process_readiness()

       name_edit.textChanged.connect(lambda: self._update_process_readiness())
       unit_edit.textChanged.connect(_on_output_unit_changed)

       self._update_unit_preview(preview_label, unit_edit.text())
       self._update_process_readiness()

   # ------------------------------------------------------------------
   # Unit preview helper (shared by both tables)
   # ------------------------------------------------------------------
   def _update_unit_preview(self, label, unit_text):
       text, is_valid = resolve_unit_preview(unit_text)
       label.setText(text)
       if not text:
           suffix = ""
       elif is_valid:
           suffix = " color: #2e7d32;"
       else:
           suffix = " color: #c62828; font-weight: bold;"
       label.setStyleSheet(UNIT_PREVIEW_STYLE + suffix)

   # ------------------------------------------------------------------
   # Input variables table management
   # ------------------------------------------------------------------
   def _update_table(self, variables):
       self._sync_master_var_data()

       self.table.setRowCount(0)
       self.table.setRowCount(len(variables))

       for row, var in enumerate(variables):
           saved = self.master_var_data.get(var, {})

           var_item = QTableWidgetItem(var)
           var_item.setFlags(var_item.flags() & ~Qt.ItemIsEditable)
           var_item.setTextAlignment(Qt.AlignCenter)
           if not self._is_used_var(var):
               var_item.setForeground(QColor("red"))
               var_item.setToolTip(
                   "Not used by the displayed function. Editable and "
                   "saved, but not part of this function's Process run."
               )
           self.table.setItem(row, COL_VAR, var_item)

           name_edit = line_edit(saved.get("name", ""),
                                 placeholder="descriptive name")
           self.table.setCellWidget(row, COL_NAME, name_edit)

           tol_combo = choice_box(TOL_TYPES, saved.get("tol", ""))
           self.table.setCellWidget(row, COL_TOL, tol_combo)

           # The three limit fields start disabled; _on_tol_changed at the
           # end of this loop enables whichever the Tol Type calls for.
           upper_edit = line_edit(saved.get("upper", ""), placeholder="numeric",
                                  validator=double_validator(), enabled=False)
           self.table.setCellWidget(row, COL_UPPER, upper_edit)

           lower_edit = line_edit(saved.get("lower", ""), placeholder="numeric",
                                  validator=double_validator(), enabled=False)
           self.table.setCellWidget(row, COL_LOWER, lower_edit)

           nominal_edit = line_edit(saved.get("nominal", ""),
                                    placeholder="numeric",
                                    validator=double_validator(), enabled=False)
           self.table.setCellWidget(row, COL_NOMINAL, nominal_edit)

           # Derived displays only — never enabled, never saved.
           # Deviation is relative to Nominal; Band is the total spread
           # between the limits and does not involve Nominal at all.
           deviation_edit = derived_display(
               "Tolerance as a deviation from Nominal, implied by the "
               "Upper/Lower limits. Calculated automatically."
           )
           self.table.setCellWidget(row, COL_DEVIATION, deviation_edit)

           band_edit = derived_display(
               "Total tolerance band: the full spread between the Upper "
               "and Lower limits, |upper - lower|. Independent of Nominal. "
               "Calculated automatically."
           )
           self.table.setCellWidget(row, COL_BAND, band_edit)

           unit_edit = line_edit(saved.get("unit", ""),
                                 placeholder=UNIT_PLACEHOLDER_HINT)
           self.table.setCellWidget(row, COL_UNIT, unit_edit)

           preview_label = unit_preview_label()
           self.table.setCellWidget(row, COL_UNIT_PREVIEW, preview_label)

           # Populated with the full list here only so a saved selection can
           # be restored; _on_tol_changed below narrows it to the options the
           # row's Tol Type actually allows.
           saved_dist = saved.get("dist", VARIABLE_DISTRIBUTIONS[0])
           dist_combo = choice_box(
               DISTRIBUTIONS,
               saved_dist if saved_dist in DISTRIBUTIONS else None
           )
           self.table.setCellWidget(row, COL_DIST, dist_combo)

           cp_validator = QDoubleValidator(MIN_CP, MAX_CP, 4)
           cp_validator.setNotation(QDoubleValidator.StandardNotation)
           cp_edit = line_edit(str(saved.get("cp", format_number(DEFAULT_CP))),
                               placeholder="1.0", validator=cp_validator,
                               tooltip=CP_TOOLTIP)
           self.table.setCellWidget(row, COL_CP, cp_edit)

           def _on_unit_changed(r=row, lbl=preview_label, edit=unit_edit):
               self._update_unit_preview(lbl, edit.text())
               self._update_process_readiness()

           tol_combo.currentTextChanged.connect(lambda _, r=row: self._on_tol_changed(r))
           upper_edit.textChanged.connect(lambda _, r=row: self._on_limit_changed(r))
           lower_edit.textChanged.connect(lambda _, r=row: self._on_limit_changed(r))
           nominal_edit.textChanged.connect(lambda _, r=row: self._on_nominal_changed(r))
           name_edit.textChanged.connect(lambda _, r=row: self._update_process_readiness())
           unit_edit.textChanged.connect(_on_unit_changed)
           dist_combo.currentTextChanged.connect(
               lambda _, r=row: (self._sync_cp_enabled(r),
                                 self._update_process_readiness()))
           cp_edit.textChanged.connect(lambda _, r=row: self._update_process_readiness())

           self._on_tol_changed(row)
           self._update_unit_preview(preview_label, unit_edit.text())

       # Refit whenever a cell's text changes -- typing a longer unit, or a
       # Tol Type switch that changes what the ± Tol cell says.
       for row in range(self.table.rowCount()):
           for col in INPUT_FIT_COLS:
               cell = self.table.cellWidget(row, col)
               if isinstance(cell, QLineEdit):
                   cell.textChanged.connect(lambda _: self._fit_input_columns())
               elif isinstance(cell, QComboBox):
                   cell.currentTextChanged.connect(lambda _: self._fit_input_columns())
       self._fit_input_columns()
       self._update_process_readiness()

   # ------------------------------------------------------------------
   # Tol Type / limit interaction logic
   # ------------------------------------------------------------------
   def _on_tol_changed(self, index):
       row = self._var_row(index)
       if not row.built:
           return

       tol = row.tol_type
       has_limits = tol not in ("", FIXED_TOL_TYPE)
       row.upper.setEnabled(has_limits)
       row.lower.setEnabled(has_limits)
       # Nominal is entered by hand except where it is derived: Symmetric
       # computes it from the limits, and a blank Tol Type has nothing to
       # enter it against yet.
       row.nominal.setEnabled(tol not in ("", "Symmetric"))

       if tol == FIXED_TOL_TYPE:
           self._mirror_limits_to_nominal(index)
       elif tol == "Symmetric":
           self._recalc_nominal(index)

       self._sync_dist_options(index)
       self._sync_cp_enabled(index)
       self._recalc_tolerance(index)
       self._update_process_readiness()

   # ------------------------------------------------------------------
   # Cp only means something for the distributions whose spread is derived
   # from the limits rather than bounded by them. Uniform and Triangular
   # fill their limits exactly by definition, and a Fixed variable has no
   # spread at all, so the field is disabled rather than silently ignored.
   # ------------------------------------------------------------------
   def _sync_cp_enabled(self, index):
       row = self._var_row(index)
       if row.dist is None or row.cp is None:
           return
       applies = row.dist_type in CP_AWARE_DISTRIBUTIONS
       row.cp.setEnabled(applies)
       # Set both ways: leaving the "not used" text in place after the row
       # switches back to Normal would describe the opposite of the truth.
       row.cp.setToolTip(
           CP_TOOLTIP if applies else
           f"Not used for {row.dist_type} \u2014 that distribution's spread "
           "is set by the limits themselves."
       )

   # ------------------------------------------------------------------
   # The Distribution column offers only what the row's Tol Type supports:
   # a Fixed variable can only be Fixed, and a variable with real limits can
   # never be Fixed. Keeping the two in sync here means the invalid
   # combinations are simply not selectable.
   # ------------------------------------------------------------------
   def _sync_dist_options(self, index):
       row = self._var_row(index)
       if row.tol is None or row.dist is None:
           return

       is_fixed = row.is_fixed
       allowed = [FIXED_DISTRIBUTION] if is_fixed else list(VARIABLE_DISTRIBUTIONS)
       existing = [row.dist.itemText(i) for i in range(row.dist.count())]

       if existing != allowed:
           previous = row.dist_type
           row.dist.blockSignals(True)
           row.dist.clear()
           row.dist.addItems(allowed)
           row.dist.setCurrentText(previous if previous in allowed else allowed[0])
           row.dist.blockSignals(False)

       # Only one legal choice when Fixed, so there is nothing to pick.
       row.dist.setEnabled(not is_fixed)

   # ------------------------------------------------------------------
   # A Fixed variable's limits ARE its Nominal — the tolerance is zero — so
   # both fields display the Nominal value instead of sitting blank. They
   # stay grayed out, and mirroring them here means any stale value from a
   # previous Tol Type is overwritten rather than left to reach the
   # simulation. Signals are blocked so this doesn't re-enter the limit
   # handler, which would try to recalculate Nominal from them.
   # ------------------------------------------------------------------
   def _mirror_limits_to_nominal(self, index):
       row = self._var_row(index)
       nominal_text = row.nominal_text
       row.set_text("upper", nominal_text, block_signals=True)
       row.set_text("lower", nominal_text, block_signals=True)

   def _recalc_nominal(self, index):
       row = self._var_row(index)
       try:
           midpoint = (float(row.upper_text) + float(row.lower_text)) / 2
       except ValueError:
           row.set_text("nominal", "")
           return
       row.set_text("nominal", format_number(midpoint))

   # ------------------------------------------------------------------
   # Derived tolerance displays.
   #
   #   ± Tol    — deviation from Nominal. Symmetric collapses to a single
   #              ±value; Asymmetric shows each side, since they differ.
   #   Tol Band — total spread |upper - lower|. Needs only the two limits,
   #              so it is computed independently of Nominal and still
   #              shows a value when Nominal is blank or mid-edit.
   # ------------------------------------------------------------------
   def _recalc_tolerance(self, index):
       row = self._var_row(index)
       if not row.built:
           return

       deviation, band = tolerance_display(
           row.tol_type, row.upper_text, row.lower_text, row.nominal_text
       )
       row.set_text("deviation", deviation)
       row.set_text("band", band)

   def _on_nominal_changed(self, index):
       if self._var_row(index).is_fixed:
           self._mirror_limits_to_nominal(index)
       self._recalc_tolerance(index)
       self._update_process_readiness()

   def _on_limit_changed(self, index):
       if self._var_row(index).tol_type == "Symmetric":
           self._recalc_nominal(index)
       self._recalc_tolerance(index)
       self._update_process_readiness()

   # ------------------------------------------------------------------
   # Row-level reason a variable isn't ready for Process (None if OK)
   # ------------------------------------------------------------------
   def _row_missing_reason(self, index):
       row = self._var_row(index)
       var_name = row.var or f"row {index + 1}"
       if not row.built:
           return f"'{var_name}': row not fully built yet."
       return variable_missing_reason(var_name, row.as_data())

   # ------------------------------------------------------------------
   # Cached-run validity
   #
   # Results and Allocate Tol both replay the last Process run rather than
   # re-sampling, so they must go dead the moment anything that fed that
   # run is edited. A signature of every input run_process reads, captured
   # at run time and compared against the live one, is what makes that
   # exact: it needs no per-widget bookkeeping, it cannot silently miss a
   # field, and an edit that is undone restores the buttons instead of
   # leaving them stuck off.
   #
   # Values are compared as raw widget text, so "1.5" and "1.50" read as a
   # change. That errs toward re-running, which is the safe direction.
   # Render style and chart style are deliberately absent: they only
   # affect how the stored values are drawn, so reopening Results simply
   # redraws in the current style.
   # ------------------------------------------------------------------
   def _current_input_signature(self):
       # The accessors return "" when no output row exists, and a row
       # always carries the name it was built from, so a blank name and no
       # row are the same thing here.
       output_row = (self._output_var_name(), self._output_unit_text().strip())

       return (
           self.current_slot,
           self.function_input.text().strip(),
           tuple(entry["edit"].text().strip() for entry in self.subfunction_rows),
           str(self.solved_expr),
           output_row,
           tuple(row.signature() for row in self._used_var_rows()),
           self.iterations_input.text().strip(),
           self.seed_input.text().strip(),
           self.eval_min_input.text().strip(),
           self.eval_max_input.text().strip(),
           self.sensitivity_method,
       )

   def _results_available(self):
       return (self._last_report_data is not None
               and self._results_signature == self._current_input_signature())

   def _update_results_availability(self):
       available = self._results_available()
       self.allocate_btn.setEnabled(available)
       self.results_btn.setEnabled(available)

   def _on_results_clicked(self):
       # The button is disabled unless this holds; the check is here so the
       # stale report can never be reopened by another code path.
       if not self._results_available():
           return
       self._show_montecarlo_results(self._last_report_data)

   # ------------------------------------------------------------------
   # Compute whether Process can run, and why not, in plain-English reasons
   # ------------------------------------------------------------------
   def _update_preview_colors(self):
       """Gray each small preview whose own field no longer matches what
       it showed at the last render -- the function's, and each
       subfunction's independently -- and black once it matches again."""
       function_changed = (
           self.last_rendered_slot != self.current_slot
           or self.last_rendered_function_text != self.function_input.text().strip()
       )
       self.function_latex_canvas.set_text_color(
           STALE_EXPRESSION_COLOR if function_changed else "black")
       for entry in self.subfunction_rows:
           changed = entry.get("rendered_text") != entry["edit"].text().strip()
           entry["preview_canvas"].set_text_color(
               STALE_EXPRESSION_COLOR if changed else "black")

   def _current_subfunction_texts(self):
       """The subfunction formulas the rendered function depends on, as
       entered. Only these can change its solved equation, so editing an
       unrelated subfunction is not a change for this slot -- nor is a
       blank row or a description. A row counts by the name it defines,
       so renaming one to or away from a name the function uses is caught.
       Before anything is rendered every subfunction counts."""
       used = self._used_subfunction_names
       texts = []
       for entry in self.subfunction_rows:
           text = entry["edit"].text().strip()
           if text and (used is None or lhs_name_of(text) in used):
               texts.append(text)
       return tuple(sorted(texts))

   def _needs_render(self):
       """Whether the equation on screen is out of date: a different slot,
       or the function or any subfunction edited since the last render."""
       return (
           self.last_rendered_function_text is None
           or self.last_rendered_slot != self.current_slot
           or self.last_rendered_function_text != self.function_input.text().strip()
           or self.last_rendered_subfunctions != self._current_subfunction_texts()
       )

   def _compute_process_readiness(self):
       reasons = []

       if self._needs_render():
           reasons.append("Click 'Render / Load Table' to apply the current function before processing.")

       if not self._used_variables:
           reasons.append("No input variables are defined — enter a function and click Render / Load Table.")
       else:
           for index in range(self.table.rowCount()):
               if not self._is_used_var(self._var_row(index).var):
                   continue
               reason = self._row_missing_reason(index)
               if reason:
                   reasons.append(reason)

       if not self._output_var_name():
           reasons.append("No output variable is defined.")
       else:
           reason = output_missing_reason({"unit": self._output_unit_text()})
           if reason:
               reasons.append(reason)

       try:
           parse_run_settings(self.iterations_input.text(), self.seed_input.text())
       except ValueError as exc:
           reasons.append(str(exc))

       return (len(reasons) == 0), reasons

   def _update_process_readiness(self):
       ready, reasons = self._compute_process_readiness()
       self._process_ready = ready
       self._process_reasons = reasons
       self.process_btn.setStyleSheet("" if ready else "color: gray;")
       # Render is gray -- but still clickable -- when the equation is
       # already up to date, so whichever button is the next step stands out.
       needs_render = self._needs_render()
       self.render_btn.setStyleSheet("" if needs_render else "color: gray;")
       # The solved equation shown is the last render; once the function
       # or a subfunction it uses is edited, it no longer matches, so it
       # is grayed until the next render.
       self.expr_preview.set_text_color(
           STALE_EXPRESSION_COLOR if needs_render and self.solved_expr is not None else "black"
       )
       self._update_preview_colors()
       # Every input-change path already routes here, so this is the one
       # place the cached run has to be re-validated.
       self._update_results_availability()

   def _on_process_clicked(self):
       self._update_process_readiness()
       if not self._process_ready:
           bullet_list = "\n".join(f"\u2022 {r}" for r in self._process_reasons)
           QMessageBox.information(
               self, "Cannot Process Yet",
               f"Process can't run yet:\n\n{bullet_list}"
           )
           return
       self.run_process()

   # ------------------------------------------------------------------
   # Process All — every slot that has a formula, summary table only
   # ------------------------------------------------------------------
   def _prepare_slot_job(self, slot_number, slot, subfunc_map):
       """Resolve one slot from stored data. Returns (job, reasons):
       a runnable job and [], or None and the reasons it can't run."""
       try:
           lhs_name, main_expr = parse_main_function(slot["text"])
           solved_expr, variables = solve_function(main_expr, subfunc_map)
       except CalculationError as exc:
           return None, [exc.message]

       reasons = []
       output_data = {}
       if lhs_name is None:
           reasons.append("No output variable \u2014 write the function as 'Name = expression'.")
       else:
           output_data = self.master_output_data.get(lhs_name, {})
           reason = output_missing_reason(output_data)
           if reason:
               reasons.append(reason)

       if not variables:
           reasons.append("No input variables are defined.")
       for var in variables:
           reason = variable_missing_reason(var, self.master_var_data.get(var, {}))
           if reason:
               reasons.append(reason)

       eval_min = eval_max = None
       try:
           eval_min, eval_max = parse_eval_limits(slot.get("eval_min", ""), slot.get("eval_max", ""))
       except ValueError as exc:
           reasons.append(str(exc))

       n_iter = seed = None
       try:
           n_iter, seed = parse_run_settings(slot.get("iterations", ""), slot.get("seed", ""))
       except ValueError as exc:
           reasons.append(str(exc))

       if reasons:
           return None, reasons

       return {
           "slot": slot_number,
           "output_var": lhs_name,
           "output_name": output_data.get("name", "").strip(),
           "output_unit_str": output_data["unit"].strip(),
           "function_desc": slot["desc"],
           "solved_expr": solved_expr,
           "var_info": build_var_info(variables, self.master_var_data),
           "eval_min": eval_min,
           "eval_max": eval_max,
           "n_iter": n_iter,
           "seed": seed,
       }, []

   def _run_slot_job(self, job):
       """Simulate one prepared slot. NaN samples are excluded and
       counted rather than prompted for. Raises CalculationError."""
       sim = self._simulate(
           job["solved_expr"], job["var_info"], job["output_unit_str"],
           job["n_iter"], job["seed"]
       )
       values = sim["values"]
       finite_mask = np.isfinite(values)
       n_invalid = int(len(values) - finite_mask.sum())
       values = values[finite_mask]

       summary = self._summarize(
           job["solved_expr"], job["var_info"], sim["symbol_order"],
           sim["output_unit"], values, job["eval_min"], job["eval_max"]
       )
       fail = eval_failure_stats(values, job["eval_min"], job["eval_max"])

       return {
           "slot": job["slot"],
           "output_var": job["output_var"],
           "output_name": job["output_name"],
           "function_desc": job["function_desc"],
           "output_unit_str": job["output_unit_str"],
           "n_iter": job["n_iter"],
           "seed": job["seed"],
           "mean": summary["mean"],
           "min": float(np.min(values)),
           "max": float(np.max(values)),
           "n_invalid": n_invalid,
           "worst_min": summary["worst_min"],
           "worst_max": summary["worst_max"],
           "worst_case_mode": summary["worst_case_mode"],
           "rss_min": summary["rss_min"],
           "rss_max": summary["rss_max"],
           "eval_min": job["eval_min"],
           "eval_max": job["eval_max"],
           "pct_total_fail": fail["pct_total_fail"],
           "cp": summary["cp"],
           "cpk": summary["cpk"],
       }

   def _variable_report_cells(self, var):
       """One row of the Process All variables table, as text, matching
       what the Input Variables table shows for that variable."""
       data = self.master_var_data.get(var, {})
       tol = data.get("tol", "")
       fixed = tol == FIXED_TOL_TYPE
       nominal = data.get("nominal", "").strip()
       # A Fixed variable's limits are its Nominal, as on screen.
       upper = nominal if fixed else data.get("upper", "").strip()
       lower = nominal if fixed else data.get("lower", "").strip()
       deviation, band = tolerance_display(tol, upper, lower, nominal)
       dist = FIXED_DISTRIBUTION if fixed else data.get("dist", "")
       # Cp only applies where the Input Variables table enables it.
       cp = (format_number(parse_cp(data.get("cp", "")))
             if dist in CP_AWARE_DISTRIBUTIONS else "\u2014")
       return [
           var, data.get("name", "").strip() or "\u2014", tol,
           upper or "\u2014", lower or "\u2014", nominal or "\u2014",
           deviation or "\u2014", band or "\u2014",
           data.get("unit", "").strip(), dist, cp,
       ]

   def _slot_label(self, slot_number):
       name = lhs_name_of(self.function_slots[slot_number]["text"])
       return f"Slot {slot_number} ({name})" if name else f"Slot {slot_number}"

   def _on_process_all_clicked(self):
       self._sync_master_var_data()
       self._sync_master_output_data()
       self._capture_current_slot()
       self._refresh_slot_appearance()

       collision_errors = self._validate_name_collisions()
       if collision_errors:
           bullet_list = "\n\n".join(f"\u2022 {e}" for e in collision_errors)
           QMessageBox.critical(
               self, "Duplicate Name",
               f"Functions cannot be processed:\n\n{bullet_list}"
           )
           return

       # Subfunctions are shared by every slot, so a broken one stops
       # the whole run rather than one slot.
       try:
           subfunc_map, _ = parse_subfunctions(
               [entry["edit"].text() for entry in self.subfunction_rows]
           )
           check_subfunction_cycle(subfunc_map)
       except CalculationError as exc:
           QMessageBox.critical(
               self, exc.title,
               "Subfunctions are shared by every function, so nothing can "
               f"be processed until this is fixed:\n\n{exc.message}"
           )
           return

       filled_slots = [
           (n, slot) for n, slot in sorted(self.function_slots.items())
           if slot["text"].strip()
       ]
       if not filled_slots:
           QMessageBox.information(self, "Nothing to Process", "No function slots have a formula.")
           return

       jobs = []
       skipped = []
       for n, slot in filled_slots:
           job, reasons = self._prepare_slot_job(n, slot, subfunc_map)
           if job is None:
               skipped.append({"label": self._slot_label(n), "reasons": reasons})
           else:
               jobs.append(job)

       if skipped:
           details = "\n\n".join(
               f"{s['label']}:\n" + "\n".join(f"   \u2022 {r}" for r in s["reasons"])
               for s in skipped
           )
           if not jobs:
               QMessageBox.information(
                   self, "Nothing to Process",
                   f"None of the functions can be processed:\n\n{details}"
               )
               return
           box = QMessageBox(self)
           box.setIcon(QMessageBox.Warning)
           box.setWindowTitle("Some Functions Will Be Skipped")
           box.setText(
               f"{len(skipped)} of {len(filled_slots)} functions have missing "
               "or invalid inputs and will be skipped. Continue with the "
               f"other {len(jobs)}?"
           )
           box.setInformativeText(details)
           continue_btn = box.addButton("Continue", QMessageBox.AcceptRole)
           box.addButton(QMessageBox.Cancel)
           box.exec_()
           if box.clickedButton() is not continue_btn:
               return

       rows = []
       failed = []
       QApplication.setOverrideCursor(Qt.WaitCursor)
       try:
           for job in jobs:
               try:
                   rows.append(self._run_slot_job(job))
               except CalculationError as exc:
                   failed.append({
                       "label": self._slot_label(job["slot"]),
                       "reasons": [f"{exc.title}: {exc.message}"],
                   })
       finally:
           QApplication.restoreOverrideCursor()

       include_eval_limits = any(
           row["eval_min"] is not None or row["eval_max"] is not None
           for row in rows
       )
       processed_slots = {row["slot"] for row in rows}
       used_vars = sorted(
           {info["var"] for job in jobs if job["slot"] in processed_slots
            for info in job["var_info"]},
           key=str.lower
       )
       report = {
           "rows": rows,
           "not_processed": skipped + failed,
           "layout": process_all_layout(include_eval_limits),
           "report_decimals": self.report_decimals,
           # Captured now, like the results, so the report shows the
           # limits the run actually used even if they are edited later.
           "variables": [self._variable_report_cells(var) for var in used_vars],
       }
       self._show_process_all_results(report)

   # ------------------------------------------------------------------
   # Save / Load
   # ------------------------------------------------------------------
   def _set_project_file(self, path):
       """Show which project file is open: set only after a save or load
       succeeds, so a failed one leaves the previous name in place."""
       self.project_file_label.setText(os.path.basename(path))
       self.project_file_label.setToolTip(path)
       self._fit_file_corner()

   def _fit_file_corner(self):
       """The menu bar gives a corner widget only its minimum width, which
       crushed the file name. Pin the minimum to the full natural width
       (it changes with the name) and re-seat the widget so the bar lays
       it out again."""
       self._file_corner.setMinimumWidth(self._file_corner.sizeHint().width())
       self.menuBar().setCornerWidget(self._file_corner, Qt.TopLeftCorner)
       self._file_corner.show()

   def save_inputs(self):
       self._sync_master_var_data()
       self._sync_master_output_data()

       self._capture_current_slot()

       has_any_function = any(slot["text"].strip() for slot in self.function_slots.values())
       if not has_any_function and not self.master_var_data \
               and not self.annotator.has_content():
           QMessageBox.warning(self, "Nothing to save", "Enter a function first.")
           return

       referenced_vars, referenced_outputs = self._compute_referenced_names()

       vars_to_save = {k: v for k, v in self.master_var_data.items() if k in referenced_vars}
       outputs_to_save = {k: v for k, v in self.master_output_data.items() if k in referenced_outputs}

       path, _ = QFileDialog.getSaveFileName(
           self, "Save Inputs", "", PROJECT_FILTER
       )
       if not path:
           return
       if not path.lower().endswith(PROJECT_EXT):
           path += PROJECT_EXT

       annotations, annotation_images = self.annotator.serialize()

       data = {
           "function_slots": {str(n): slot for n, slot in self.function_slots.items() if slot["text"].strip()},
           "current_slot": self.current_slot,
           "subfunctions": [
               {"text": e["edit"].text(), "desc": e["desc_edit"].text()}
               for e in self.subfunction_rows
           ],
           # Iterations, seed and the eval limits ride inside each slot.
           "sensitivity_method": self.sensitivity_method,
           "report_decimals": self.report_decimals,
           "render_style": self.render_style,
           "chart_style": self.chart_style,
           "outputs": outputs_to_save,
           "variables": vars_to_save,
           "annotations": annotations,
       }

       try:
           with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
               zf.writestr(PROJECT_JSON, json.dumps(data, indent=2))
               # PNGs are already compressed; deflating again just burns time.
               for arcname, blob in annotation_images.items():
                   zf.writestr(arcname, blob,
                               compress_type=zipfile.ZIP_STORED)
           self._set_project_file(path)
           QMessageBox.information(self, "Saved", f"Project saved to:\n{path}")
       except Exception as exc:
           QMessageBox.critical(self, "Save Error", str(exc))

   def load_inputs(self):
       path, _ = QFileDialog.getOpenFileName(
           self, "Load Inputs", "", PROJECT_FILTER
       )
       if not path:
           return

       try:
           with zipfile.ZipFile(path) as zf:
               data = json.loads(zf.read(PROJECT_JSON).decode("utf-8"))
               annotation_images = {
                   name: zf.read(name)
                   for name in zf.namelist()
                   if name.startswith(PROJECT_IMAGE_DIR)
               }
       except Exception as exc:
           QMessageBox.critical(self, "Load Error", str(exc))
           return

       self._used_variables = None
       self.table.setRowCount(0)
       self.output_table.setRowCount(0)
       self.last_rendered_slot = None
       self.last_rendered_function_text = None
       # The subfunctions as they were at that render. Editing one changes
       # the solved equation as much as editing the function does.
       self.last_rendered_subfunctions = None
       # Whatever was on screen belongs to the project being replaced. The
       # render below refills these from the loaded slot; a project whose
       # current slot is empty has nothing to render and nothing to show.
       self._clear_sim_fields()

       self.master_var_data = data.get("variables", {})
       self.master_output_data = data.get("outputs", {})

       self.function_slots = {n: new_slot() for n in range(1, MAX_FUNCTION_SLOTS + 1)}
       for n_str, slot in data.get("function_slots", {}).items():
           n = int(n_str)
           if not 1 <= n <= MAX_FUNCTION_SLOTS:
               continue
           entry = new_slot(slot.get("text", ""), slot.get("desc", ""))
           for key in SLOT_SIM_FIELDS:
               if key in slot:
                   entry[key] = str(slot[key])
           self.function_slots[n] = entry

       self.clear_subfunction_rows()
       for sub in data.get("subfunctions", []):
           self.add_subfunction_row(sub.get("text", ""), sub.get("desc", ""))

       current_slot = int(data.get("current_slot", 1))
       current_slot = max(1, min(MAX_FUNCTION_SLOTS, current_slot))
       self.current_slot = current_slot
       self.slot_combo.blockSignals(True)
       self.slot_combo.setCurrentIndex(current_slot - 1)
       self.slot_combo.blockSignals(False)

       slot_data = self.function_slots[current_slot]
       self.function_input.setText(slot_data["text"])
       self.function_desc_input.setText(slot_data["desc"])

       self._refresh_slot_appearance()

       if "render_style" in data and data["render_style"] in RENDER_STYLES:
           self.render_style = data["render_style"]
       if "chart_style" in data and data["chart_style"] in DISTRIBUTION_CHART_STYLES:
           self.chart_style = data["chart_style"]

       if slot_data["text"].strip():
           self.process_function()

       if data.get("sensitivity_method") in SENSITIVITY_METHODS:
           self.sensitivity_method = data["sensitivity_method"]
       decimals = data.get("report_decimals", DEFAULT_REPORT_DECIMALS)
       if isinstance(decimals, int) and 0 <= decimals <= MAX_REPORT_DECIMALS:
           self.report_decimals = decimals

       annotation_warnings = self.annotator.load_state(
           data.get("annotations"), annotation_images
       )

       self._update_process_readiness()

       self._set_project_file(path)
       message = f"Project loaded from:\n{path}"
       if annotation_warnings:
           message += "\n\nAnnotation issues:\n" + "\n".join(annotation_warnings)
       QMessageBox.information(self, "Loaded", message)

   # ------------------------------------------------------------------
   # Monte Carlo sampling (seeded via injected rng)
   # ------------------------------------------------------------------
   def _sample_distribution(self, rng, dist, nominal, lower, upper, n,
                            cp=DEFAULT_CP):
       """Draw n samples for one variable.

       `cp` is the process capability of the supplying process and applies
       only to Normal and Lognormal, where sigma is derived from the
       tolerance width: width = 6 * Cp * sigma. Cp = 1.00 reproduces the
       usual limits-are-+/-3-sigma assumption; a capable process (Cp > 1)
       is genuinely tighter than its tolerance, and treating it as Cp = 1
       overstates its contribution to the output spread.
       """
       if dist == FIXED_DISTRIBUTION:
           return np.full(n, nominal, dtype=float)

       if dist == "Normal":
           sigma = (upper - lower) / (6.0 * cp)
           if sigma <= 0:
               raise ValueError("Upper limit must exceed Lower limit for a Normal distribution.")
           return rng.normal(loc=nominal, scale=sigma, size=n)

       if dist == "Uniform":
           if upper <= lower:
               raise ValueError("Upper limit must exceed Lower limit for a Uniform distribution.")
           return rng.uniform(low=lower, high=upper, size=n)

       if dist == "Triangular":
           if not (lower <= nominal <= upper):
               raise ValueError("Nominal must lie between Lower and Upper for a Triangular distribution.")
           if upper <= lower:
               raise ValueError("Upper limit must exceed Lower limit for a Triangular distribution.")
           return rng.triangular(left=lower, mode=nominal, right=upper, size=n)

       if dist == "Lognormal":
           if lower <= 0 or nominal <= 0:
               raise ValueError("Lognormal distribution requires positive Lower limit and Nominal.")
           mu = np.log(nominal)
           sigma = (np.log(upper) - np.log(lower)) / (6.0 * cp)
           if sigma <= 0:
               raise ValueError("Upper limit must exceed Lower limit for a Lognormal distribution.")
           return rng.lognormal(mean=mu, sigma=sigma, size=n)

       raise ValueError(f"Unknown distribution type: {dist}")

   # ------------------------------------------------------------------
   # Sensitivity: Pearson correlation
   # ------------------------------------------------------------------
   def _compute_pearson(self, input_samples, output_values):
       sens = {}
       out_std = np.std(output_values)
       for var, samples in input_samples.items():
           in_std = np.std(samples)
           if in_std == 0 or out_std == 0:
               corr = 0.0
           else:
               corr = float(np.corrcoef(samples, output_values)[0, 1])
               if np.isnan(corr):
                   corr = 0.0
           sens[var] = corr
       return sens

   # ------------------------------------------------------------------
   # Sensitivity: Standardized Regression Coefficients
   # ------------------------------------------------------------------
   def _compute_src(self, input_samples, output_values):
       vars_list = list(input_samples.keys())
       X = np.column_stack([input_samples[v] for v in vars_list]).astype(float)
       X_std = np.zeros_like(X)
       for i in range(X.shape[1]):
           std = X[:, i].std()
           X_std[:, i] = (X[:, i] - X[:, i].mean()) / std if std > 0 else 0.0

       y = np.asarray(output_values, dtype=float)
       y_std_dev = y.std()
       y_std = (y - y.mean()) / y_std_dev if y_std_dev > 0 else np.zeros_like(y)

       coeffs, _, _, _ = np.linalg.lstsq(X_std, y_std, rcond=None)
       y_pred = X_std @ coeffs
       ss_res = float(np.sum((y_std - y_pred) ** 2))
       ss_tot = float(np.sum((y_std - np.mean(y_std)) ** 2))
       r2 = (1 - ss_res / ss_tot) if ss_tot > 0 else 0.0

       src = dict(zip(vars_list, coeffs.tolist()))
       return src, r2

   # ------------------------------------------------------------------
   # Sensitivity: percent contribution to output variance
   #
   # A standardized regression coefficient squared is that variable's share
   # of the explained variance, so normalizing the squares gives shares
   # that sum to 100%. Reports the same information as SRC but in the form
   # an engineer acts on -- "this variable accounts for 84% of the spread"
   # rather than "this variable correlates -0.913".
   #
   # Only as trustworthy as the linear fit behind it, so the R^2 is carried
   # through to the chart title -- a low R^2 means variance is going into
   # nonlinear or interaction effects that these shares cannot see.
   # ------------------------------------------------------------------
   def _compute_variance_contribution(self, input_samples, output_values):
       src, r2 = self._compute_src(input_samples, output_values)
       squares = {v: c * c for v, c in src.items()}
       total = sum(squares.values())
       if total <= 0:
           return {v: 0.0 for v in squares}, r2
       return {v: 100.0 * sq / total for v, sq in squares.items()}, r2

   # ------------------------------------------------------------------
   # Convergence: turn sampling error into a decision
   #
   # A standard error of "+/-0.0219 psi" is only useful to someone who
   # already knows how to act on it. What the user actually wants to know
   # is whether to change the iteration count, so this compares each
   # estimate's error against a target precision and reports a verdict
   # plus the count that would satisfy it.
   #
   # The failure rate almost always dominates: an average settles quickly,
   # but a rare event needs roughly 100 occurrences before its rate is
   # stable, so a 0.3% failure rate needs tens of thousands of samples
   # while the mean is fine on a few thousand.
   # ------------------------------------------------------------------
   @staticmethod
   def _round_iterations(value):
       """Round up to a count a person would actually type."""
       value = max(int(math.ceil(value)), CONV_MIN_SUGGESTION)
       power = 10 ** int(math.floor(math.log10(value)))
       for step in (1, 2, 5, 10):
           candidate = step * power
           if candidate >= value:
               return int(candidate)
       return int(10 * power)

   def _convergence_text(self, values, n, output_unit_str, pct_total_fail, decimals):
       def num(value):
           return format_report_number(value, decimals)

       mean_val = float(np.mean(values))
       std_val = float(np.std(values))

       if std_val <= 0:
           return ("Every iteration produced the same value, so there is no "
                   "sampling error to report.\n")

       se_mean = std_val / math.sqrt(n)
       se_std = std_val / math.sqrt(2.0 * n)

       # Iterations each estimate needs to reach the target precision,
       # expressed as a fraction of the output's own standard deviation.
       need_mean = 1.0 / (CONV_SIGMA_PRECISION ** 2)
       need_std = 1.0 / (2.0 * CONV_SIGMA_PRECISION ** 2)
       need_fail = None

       lines = [
           f"Mean:     {num(mean_val)} \u00b1 {num(se_mean)} {output_unit_str}",
           f"Std Dev:  {num(std_val)} \u00b1 {num(se_std)} {output_unit_str}",
       ]

       if pct_total_fail is not None:
           frac = pct_total_fail / 100.0
           se_fail = 100.0 * math.sqrt(max(frac * (1 - frac), 0.0) / n)
           lines.append(
               f"Failure:  {num(pct_total_fail)}% \u00b1 {num(se_fail)} "
               f"percentage points"
           )
           if frac > 0:
               need_fail = (1.0 - frac) / (frac * CONV_FAIL_REL_ERROR ** 2)

       text = "\n".join(lines) + "\n"
       text += ("(\u00b1 is how much each figure moves if you re-run with a "
                "different seed)\n")

       required = max(need_mean, need_std, *( [need_fail] if need_fail else [] ))
       suggestion = self._round_iterations(required)

       if n >= required:
           verdict = f"VERDICT: {n:,} iterations is enough."
           # Only advise cutting back when the saving is worth the click.
           if n >= CONV_OVERKILL_FACTOR * required:
               verdict += (f" You could use as few as {suggestion:,} "
                           f"and get the same answer.")
           text += verdict + "\n"
       else:
           text += (
               f"VERDICT: {n:,} iterations is NOT enough. "
               f"Increase to about {suggestion:,}.\n"
           )
           if need_fail and need_fail > max(need_mean, need_std):
               text += (
                   f"  The mean and spread are already settled; it is the "
                   f"{num(pct_total_fail)}% failure rate that needs more "
                   f"samples. A rare event needs roughly "
                   f"{round(CONV_FAIL_REL_ERROR ** -2)} occurrences before its "
                   f"rate is stable.\n"
               )

       if pct_total_fail == 0.0:
           # Zero failures gives no rate to put an error bar on, so say what
           # the run does establish -- an upper bound -- rather than claiming
           # the failure rate is settled.
           text += ("  No failures occurred. That bounds the true rate below "
                    f"roughly {num(300.0 / n)}%, but does not pin it down; "
                    "raise iterations only if you need a tighter bound.\n")

       return text

   # ------------------------------------------------------------------
   # Tolerance allocation (inverse solve)
   #
   # The forward question is "given these tolerances, what output spread do
   # I get?". This answers the design question instead: "I need the output
   # inside these limits at this Cpk -- what tolerance can each variable
   # have?"
   #
   # Built on the OAT swings and the RSS model, so it inherits their
   # linearity assumption: each variable's output contribution is taken as
   # proportional to its tolerance. That holds well over tolerance-sized
   # bands even for a nonlinear equation, but the result is a starting
   # point to verify with a fresh Monte Carlo run, not a final answer.
   # ------------------------------------------------------------------
   def _allocate_tolerances(self, var_info, oat_results, oat_baseline,
                            target_min, target_max, target_cpk):
       if target_max <= target_min:
           raise ValueError("Target Max must exceed Target Min.")
       if target_cpk <= 0:
           raise ValueError("Target Cpk must be greater than zero.")

       # Cpk is governed by whichever limit the baseline sits closer to, so
       # that margin is what sets the allowable sigma. Using the full width
       # would quietly permit an off-centre design to miss the near limit.
       margin = min(oat_baseline - target_min, target_max - oat_baseline)
       if margin <= 0:
           raise ValueError(
               f"The all-nominal output ({oat_baseline:.6g}) falls outside "
               f"[{target_min:.6g}, {target_max:.6g}]. No tolerance "
               "allocation can fix a centring problem \u2014 adjust the "
               "nominals first."
           )
       allowed_sigma = margin / (3.0 * target_cpk)

       # Present per-variable one-sigma output contributions.
       rows = []
       for info in var_info:
           var = info["var"]
           if info.get("fixed") or var not in oat_results:
               continue
           lo, hi = oat_results[var]
           swing = (abs(lo) + abs(hi)) / 2.0
           cp = info.get("cp", DEFAULT_CP)
           sigma_i = swing / (3.0 * cp)
           if sigma_i <= 0:
               continue
           rows.append({
               "var": var,
               "half_tol": (info["upper"] - info["lower"]) / 2.0,
               "sigma": sigma_i,
               "cp": cp,
           })

       if not rows:
           raise ValueError(
               "No variable has any tolerance to allocate. Every variable "
               "is Fixed or has zero sensitivity to the output."
           )

       current_sigma = math.sqrt(sum(r["sigma"] ** 2 for r in rows))
       if current_sigma <= 0:
           raise ValueError("Current RSS sigma is zero; nothing to scale.")

       # Uniform: scale every tolerance by the same factor. Keeps the
       # relative tolerance scheme the designer already chose.
       uniform_factor = allowed_sigma / current_sigma

       # Equal-contribution: give every variable the same share of the
       # variance budget, so a sensitive variable is tightened and an
       # insensitive one is loosened. Usually the cheaper scheme to buy.
       per_var_sigma = allowed_sigma / math.sqrt(len(rows))

       for r in rows:
           r["share_now"] = 100.0 * (r["sigma"] / current_sigma) ** 2
           r["uniform_half_tol"] = r["half_tol"] * uniform_factor
           factor = per_var_sigma / r["sigma"]
           r["equal_factor"] = factor
           r["equal_half_tol"] = r["half_tol"] * factor

       rows.sort(key=lambda r: -r["share_now"])
       return {
           "rows": rows,
           "baseline": oat_baseline,
           "target_min": target_min,
           "target_max": target_max,
           "target_cpk": target_cpk,
           "margin": margin,
           "allowed_sigma": allowed_sigma,
           "current_sigma": current_sigma,
           "current_cpk": margin / (3.0 * current_sigma),
           "uniform_factor": uniform_factor,
       }

   # ------------------------------------------------------------------
   # Bootstrap confidence intervals
   # ------------------------------------------------------------------
   def _bootstrap_estimator(self, method):
       """The function that recomputes `method` on a resampled draw.

       A dict rather than an if/else chain: a newly added method that is
       missing here returns None and simply gets no error bars, instead of
       silently being handed another method's estimator.
       """
       estimators = {
           METHOD_PEARSON: self._compute_pearson,
           METHOD_SRC: lambda X, y: self._compute_src(X, y)[0],
           METHOD_CONTRIBUTION:
               lambda X, y: self._compute_variance_contribution(X, y)[0],
       }
       return estimators.get(method)

   def _bootstrap_ci(self, input_samples, output_values, method,
                      n_boot=N_BOOTSTRAP, ci_pct=BOOTSTRAP_CI_PCT):
       rng = np.random.default_rng()
       vars_list = list(input_samples.keys())
       n = len(output_values)
       boot = {v: [] for v in vars_list}

       estimator = self._bootstrap_estimator(method)
       if estimator is None:
           return None

       for _ in range(n_boot):
           idx = rng.integers(0, n, n)
           boot_samples = {v: input_samples[v][idx] for v in vars_list}
           boot_out = output_values[idx]
           vals = estimator(boot_samples, boot_out)
           for v in vars_list:
               boot[v].append(vals[v])

       lo_pct = (100 - ci_pct) / 2
       hi_pct = 100 - lo_pct
       ci = {}
       for v in vars_list:
           arr = np.array(boot[v])
           ci[v] = (float(np.percentile(arr, lo_pct)), float(np.percentile(arr, hi_pct)))
       return ci

   # ------------------------------------------------------------------
   # Sensitivity: Sobol indices (separate dedicated sampling)
   # ------------------------------------------------------------------
   def _compute_sobol(self, var_info, expr, symbol_order, output_unit, n_base=SOBOL_BASE_SAMPLES):
       try:
           from SALib.sample import saltelli
           from SALib.analyze import sobol as sobol_analyze
       except ImportError:
           raise RuntimeError(
               "Sobol indices require the SALib package.\n"
               "Install it with:  pip install SALib"
           )

       # A Fixed variable has zero width, which Saltelli cannot sample and
       # which would yield a meaningless index. It is held at Nominal and
       # left out of the problem definition entirely.
       varying = [info for info in var_info if not info.get("fixed")]
       if not varying:
           raise RuntimeError(
               "Sobol indices need at least one variable that isn't Fixed."
           )

       problem = {
           "num_vars": len(varying),
           "names": [info["var"] for info in varying],
           "bounds": [[info["lower"], info["upper"]] for info in varying],
       }
       param_values = saltelli.sample(problem, n_base, calc_second_order=False)

       func = compile_expression(expr, symbol_order)

       quantities = []
       col_index = 0
       for info in var_info:
           if info.get("fixed"):
               value = info["nominal"]
           else:
               value = param_values[:, col_index]
               col_index += 1
           quantities.append(to_calculation_units(ureg.Quantity(value, info["unit"])))

       result = func(*quantities).to(output_unit)
       Y = np.asarray(result.magnitude, dtype=float)

       # Saltelli lays the samples out in consecutive groups of D + 2 rows,
       # one group per base point, and the estimator compares rows within
       # a group. A single undefined row therefore spoils its whole group,
       # so groups are kept or dropped whole and the layout stays intact.
       group_size = problem["num_vars"] + 2
       groups = Y.reshape(-1, group_size)
       valid = np.isfinite(groups).all(axis=1)
       n_groups, n_valid = len(groups), int(valid.sum())
       if n_valid < SOBOL_MIN_VALID_FRACTION * n_groups:
           raise RuntimeError(
               f"Sobol indices can't be computed: the output is undefined "
               f"(NaN) in {n_groups - n_valid:,} of {n_groups:,} sample "
               f"groups, leaving too few to estimate from. Sobol samples "
               f"every variable uniformly across its full limits, so it "
               f"reaches combinations the Monte Carlo draw rarely does. "
               f"Use another sensitivity method, or narrow the limits of the "
               f"variables feeding any sqrt() or log() term, or close any gap "
               f"between a Piecewise's conditions."
           )

       Si = sobol_analyze.analyze(problem, groups[valid].ravel(),
                                  calc_second_order=False, print_to_console=False)
       s1 = dict(zip(problem["names"], Si["S1"].tolist()))
       st = dict(zip(problem["names"], Si["ST"].tolist()))
       return s1, st, n_groups - n_valid, n_groups

   # ------------------------------------------------------------------
   # Every variable held at Nominal, in calculation units. The starting
   # point for both one-at-a-time analyses below.
   # ------------------------------------------------------------------
   @staticmethod
   def _nominal_quantities(var_info):
       return [
           to_calculation_units(ureg.Quantity(info["nominal"], info["unit"]))
           for info in var_info
       ]

   # ------------------------------------------------------------------
   # OAT sweep — always computed (feeds tornado chart, RSS, worst-case fallback)
   # ------------------------------------------------------------------
   def _compute_oat_sensitivity(self, var_info, expr, symbol_order, output_unit):
       func = compile_expression(expr, symbol_order)
       nominal_qtys = self._nominal_quantities(var_info)
       baseline = float(func(*nominal_qtys).to(output_unit).magnitude)

       results = {}
       for i, info in enumerate(var_info):
           low_qtys = list(nominal_qtys)
           low_qtys[i] = to_calculation_units(ureg.Quantity(info["lower"], info["unit"]))
           low_val = func(*low_qtys).to(output_unit).magnitude

           high_qtys = list(nominal_qtys)
           high_qtys[i] = to_calculation_units(ureg.Quantity(info["upper"], info["unit"]))
           high_val = func(*high_qtys).to(output_unit).magnitude

           results[info["var"]] = (float(low_val - baseline), float(high_val - baseline))

       return baseline, results

   # ------------------------------------------------------------------
   # Sensitivity: elasticity (percent out per percent in)
   #
   # Every other method here normalises by spread -- how much of the
   # output's uncertainty each input is responsible for. Elasticity
   # normalises by level: if this input were 1% different, how far would
   # the output move, in percent? An input can be known precisely
   # (negligible variance share) and still be the biggest lever, or the
   # reverse, so the two can rank the same model very differently.
   #
   # Derived rather than measured, as  d ln y / d ln x = x * (dy/dx) / y.
   # sympy differentiates the expression the run actually evaluated, so a
   # power law returns the exponent itself -- 2, -4, not 2.0000003 -- and
   # whether it IS a power law is answered by asking the derived
   # expression whether it still contains variables. No step size, no
   # tolerance, and no dependence on the sampled draw or the seed.
   #
   # Suppressed rather than reported where the arithmetic is undefined: an
   # output at or near zero sends the ratio to infinity, which is the
   # normal case for a clearance or margin designed to sit near zero. An
   # input passing through zero needs no special case -- the exact
   # derivative gives an elasticity of zero there, where a finite
   # difference would have had nothing to divide by.
   # ------------------------------------------------------------------
   def _elasticity_probes(self, info):
       """Points to read the elasticity at, Nominal last.

       Nominal is the one reported; the rest only say how far it travels.
       The middle of the sweep would sit at the midpoint of the limits,
       which is not Nominal under an asymmetric tolerance and would put
       this method on a different anchor from OAT, RSS and worst-case.

       A Fixed variable gets Nominal alone. Still worth reporting:
       elasticity asks what a different design value would do, which is a
       fair question to ask of a pinned one.
       """
       nominal = float(info["nominal"])
       if info.get("fixed"):
           return np.array([nominal], dtype=float)
       return np.append(
           np.linspace(float(info["lower"]), float(info["upper"]),
                       ELASTICITY_PROBE_POINTS),
           nominal,
       )

   def _sweep_one(self, expr, symbol_order, index, info, nominal_qtys,
                  probes, unit=None):
       """Evaluate `expr` with variable `index` swept over `probes`."""
       args = list(nominal_qtys)
       args[index] = to_calculation_units(ureg.Quantity(probes, info["unit"]))
       result = compile_expression(expr, symbol_order)(*args)
       if unit is not None:
           result = result.to(unit)
       magnitude = getattr(result, "magnitude", result)
       return np.broadcast_to(np.asarray(magnitude, dtype=float),
                              probes.shape).astype(float)

   def _elasticity_of(self, expr, symbol_order, index, info, nominal_qtys,
                      output_unit, affine):
       """One variable's elasticity, its range, and which of those it is.

       Raises ValueError with a printable phrase when the quantity has no
       meaning for this variable, which the caller turns into the note
       shown in place of a bar.
       """
       symbol = symbol_order[index]
       if symbol not in expr.free_symbols:
           return 0.0, (0.0, 0.0), ELASTICITY_REGIME_NONE

       ratio = elasticity_ratio(expr, symbol)
       constant = not ratio.free_symbols

       probes = self._elasticity_probes(info)
       outputs = self._sweep_one(expr, symbol_order, index, info,
                                 nominal_qtys, probes, output_unit)

       if not np.all(np.isfinite(outputs)):
           raise ValueError("the output is undefined somewhere in this range")
       scale = float(np.max(np.abs(outputs)))
       if scale == 0.0 or float(np.min(outputs)) * float(np.max(outputs)) <= 0.0:
           raise ValueError("the output crosses zero across this range")
       if float(np.min(np.abs(outputs))) < ELASTICITY_NEAR_ZERO_FRAC * scale:
           raise ValueError("the output approaches zero across this range")

       if constant:
           local = np.full(probes.shape, float(ratio))
       else:
           # The ratio is dimensionless by construction, so pint hands back
           # either a plain array or a dimensionless quantity depending on
           # whether the units cancelled before or during evaluation.
           local = self._sweep_one(ratio, symbol_order, index, info,
                                   nominal_qtys, probes,
                                   ureg.dimensionless)
       if not np.all(np.isfinite(local)):
           raise ValueError("the elasticity is undefined somewhere in this range")

       value = float(local[-1])
       span = (float(np.min(local)), float(np.max(local)))

       if constant and value == 0.0:
           regime = ELASTICITY_REGIME_NONE
       elif affine:
           regime = ELASTICITY_REGIME_AFFINE
       elif constant:
           regime = ELASTICITY_REGIME_CONSTANT
       elif info.get("fixed"):
           regime = ELASTICITY_REGIME_POINT
       else:
           regime = ELASTICITY_REGIME_LOCAL
       return value, span, regime

   @staticmethod
   def _elasticity_note(regime, var, value, span):
       if regime == ELASTICITY_REGIME_NONE:
           return "no effect \u2014 the output does not move with this input"
       if regime == ELASTICITY_REGIME_AFFINE:
           return ("linear stack-up \u2014 this is the term's share of the "
                   "nominal total, already covered by Variance Contribution")
       if regime == ELASTICITY_REGIME_CONSTANT:
           return f"constant exponent \u2014 output \u221d {var}^{value:.4g}"
       if regime == ELASTICITY_REGIME_POINT:
           return "Fixed input \u2014 read at Nominal only, no range to check"
       return (f"local only \u2014 runs {span[0]:+.3g} to {span[1]:+.3g} "
               f"across this input's range")

   def _compute_elasticity(self, var_info, expr, symbol_order, output_unit):
       nominal_qtys = self._nominal_quantities(var_info)
       affine = self._is_affine(expr, symbol_order)

       elasticities = {}
       spans = {}
       notes = {}
       regimes = {}

       for index, info in enumerate(var_info):
           var = info["var"]
           try:
               value, span, regime = self._elasticity_of(
                   expr, symbol_order, index, info, nominal_qtys,
                   output_unit, affine
               )
           except ValueError as exc:
               notes[var] = f"not defined \u2014 {exc}"
               continue
           elasticities[var] = value
           spans[var] = span
           regimes[var] = regime
           notes[var] = self._elasticity_note(regime, var, value, span)

       if not elasticities:
           raise RuntimeError(
               "No variable has a defined elasticity.\n\n"
               + "\n".join(f"  {v}: {n}" for v, n in notes.items())
               + "\n\nElasticity needs an output that stays strictly "
                 "positive or strictly negative. A clearance or margin sitting "
                 "near zero has no meaningful percentage change \u2014 use the OAT "
                 "tornado or Variance Contribution for that."
           )

       # A variable the output ignores says nothing about which regime the
       # rest are in, so it is left out of this verdict rather than
       # dragging an otherwise exact panel down to "local".
       kinds = set(regimes.values()) - {ELASTICITY_REGIME_NONE}
       if not kinds:
           summary = "no input moves the output"
       elif kinds == {ELASTICITY_REGIME_AFFINE}:
           summary = "linear stack-up, equals share of nominal"
       elif kinds <= {ELASTICITY_REGIME_CONSTANT, ELASTICITY_REGIME_POINT}:
           summary = "exact exponents"
       else:
           summary = "local, at nominal"

       return elasticities, spans, notes, summary

   # ------------------------------------------------------------------
   # RSS estimate (reuses OAT swings, treats limits as +/-3 sigma)
   # ------------------------------------------------------------------
   def _compute_rss(self, oat_results, cp_map=None):
       """RSS output sigma from the one-at-a-time swings.

       Each variable's limits span 6 * Cp * sigma, so its one-sigma output
       contribution is its mean OAT swing divided by 3 * Cp. Ignoring Cp
       here would contradict the sampler, which does honour it -- the RSS
       estimate and the Monte Carlo standard deviation would then disagree
       for no visible reason.
       """
       cp_map = cp_map or {}
       total_var = 0.0
       for var, (lo, hi) in oat_results.items():
           cp = cp_map.get(var, DEFAULT_CP)
           one_sigma = (abs(lo) + abs(hi)) / 2.0 / (3.0 * cp)
           total_var += one_sigma ** 2
       return total_var ** 0.5

   # ------------------------------------------------------------------
   # Worst-case stack-up: exact enumeration when feasible, generic fallback
   # otherwise. Works for any function/variable-count.
   # ------------------------------------------------------------------
   def _compute_worst_case(self, var_info, expr, symbol_order, output_unit,
                            oat_baseline, oat_results):
       # A Fixed variable has a single value, so it adds no corner to
       # enumerate. Counting only the varying ones keeps the exact method
       # available for cases that would otherwise trip the limit.
       varying = [info for info in var_info if not info.get("fixed")]
       n = len(varying)

       if n == 0:
           return oat_baseline, oat_baseline, WORST_CASE_EXACT_LINEAR

       # Independent-swing formula. For an affine expression each variable's
       # contribution doesn't depend on where the others sit, so summing the
       # single-variable OAT swings lands exactly on the true worst corner --
       # no enumeration needed. For anything else it's only an estimate,
       # because cross terms make the corners interact.
       swing_max = oat_baseline + sum(max(lo, hi) for lo, hi in oat_results.values())
       swing_min = oat_baseline + sum(min(lo, hi) for lo, hi in oat_results.values())

       if self._is_affine(expr, symbol_order):
           return swing_min, swing_max, WORST_CASE_EXACT_LINEAR

       if n <= WORST_CASE_EXACT_LIMIT:
           return self._enumerate_worst_case(var_info, expr, symbol_order, output_unit)

       return swing_min, swing_max, WORST_CASE_APPROXIMATE

   # ------------------------------------------------------------------
   # An expression is affine (linear plus a constant) exactly when every
   # first derivative is free of the variables -- a single A*B or A**2
   # leaves one behind and disqualifies it. Checked symbolically, before
   # any numeric work, so the affine shortcut above is only ever taken
   # when it is provably exact. Any failure to decide is treated as
   # non-affine, which costs time but never accuracy.
   #
   # Cheap enough (one diff per variable, no simplify) that the two
   # callers can each ask independently rather than threading the answer
   # through their signatures.
   # ------------------------------------------------------------------
   def _is_affine(self, expr, symbol_order):
       try:
           free = set(symbol_order) & expr.free_symbols
           for var in free:
               if sympy.diff(expr, var).free_symbols & free:
                   return False
           return True
       except Exception:
           return False

   # ------------------------------------------------------------------
   # Exact enumeration of all 2^n limit corners.
   #
   # Units are resolved to calculation units ONCE per variable, then every
   # corner is evaluated in a single vectorized call per chunk. Building
   # pint Quantities inside a per-corner Python loop instead costs ~4us per
   # construction, which at 16 variables and 2^15 corners is over half a
   # million constructions -- seconds of work to wrap arithmetic that takes
   # microseconds. Chunking keeps peak memory flat regardless of n rather
   # than materializing all corners at once.
   # ------------------------------------------------------------------
   def _enumerate_worst_case(self, var_info, expr, symbol_order, output_unit):
       func = compile_expression(expr, symbol_order)

       varying_idx = [i for i, info in enumerate(var_info) if not info.get("fixed")]
       n = len(varying_idx)

       # One unit conversion per variable per bound, not per corner.
       resolved = []
       for info in var_info:
           if info.get("fixed"):
               fixed_qty = to_calculation_units(
                   ureg.Quantity(info["nominal"], info["unit"])
               )
               resolved.append((fixed_qty, None, None))
           else:
               low_qty = to_calculation_units(ureg.Quantity(info["lower"], info["unit"]))
               high_qty = to_calculation_units(ureg.Quantity(info["upper"], info["unit"]))
               resolved.append((None, low_qty, high_qty))

       total = 1 << n
       bit_weights = np.arange(n, dtype=np.uint64)

       worst_min = math.inf
       worst_max = -math.inf

       for start in range(0, total, WORST_CASE_CHUNK):
           stop = min(start + WORST_CASE_CHUNK, total)
           corner_ids = np.arange(start, stop, dtype=np.uint64)
           # Row r, column c holds bit c of corner index r: 1 selects the
           # upper limit for that variable, 0 the lower.
           bits = ((corner_ids[:, None] >> bit_weights) & np.uint64(1)).astype(bool)

           args = []
           col = 0
           for i, info in enumerate(var_info):
               fixed_qty, low_qty, high_qty = resolved[i]
               if fixed_qty is not None:
                   args.append(fixed_qty)
               else:
                   mags = np.where(
                       bits[:, col], high_qty.magnitude, low_qty.magnitude
                   )
                   args.append(ureg.Quantity(mags, low_qty.units))
                   col += 1

           chunk = func(*args).to(output_unit).magnitude
           chunk = np.asarray(chunk, dtype=float)
           worst_min = min(worst_min, float(np.min(chunk)))
           worst_max = max(worst_max, float(np.max(chunk)))

       return worst_min, worst_max, WORST_CASE_EXACT_ENUMERATED

   # ------------------------------------------------------------------
   # The chart panel for the selected sensitivity method.
   #
   # Four of the six produce the same shape -- one bar per variable, with
   # optional whiskers -- and were four copies of the same dict differing
   # only in the numbers and the labels. Split into "what does this method
   # measure" and "how is a bar chart described", so adding a method means
   # adding a branch rather than copying a dict and hoping every key came
   # along.
   #
   # Raises rather than reporting: the caller owns the error dialog,
   # because a failure here means the whole run has nothing to show.
   # ------------------------------------------------------------------
   def _build_sensitivity_panel(self, method, var_info, symbol_order,
                                output_unit, output_var, output_unit_str,
                                input_samples, values, oat_baseline,
                                oat_results):
       if method in BOOTSTRAP_METHODS:
           if method == METHOD_PEARSON:
               panel_values = self._compute_pearson(input_samples, values)
               title = (f"MC Sensitivity — Pearson Correlation "
                        f"({BOOTSTRAP_CI_PCT}% CI)")
               xlabel = f"Correlation with {output_var}"
               xlim = SENSITIVITY_XLIM
           elif method == METHOD_SRC:
               panel_values, r2 = self._compute_src(input_samples, values)
               title = (f"MC Sensitivity — SRC  (R\u00b2 = {r2:.4f}, "
                        f"{BOOTSTRAP_CI_PCT}% CI)")
               xlabel = f"Standardized coefficient on {output_var}"
               xlim = SENSITIVITY_XLIM
           else:
               panel_values, r2 = self._compute_variance_contribution(
                   input_samples, values
               )
               title = (f"Contribution to Variance  "
                        f"(linear R\u00b2 = {r2:.4f}, {BOOTSTRAP_CI_PCT}% CI)")
               xlabel = "Share of output variance (%)"
               # Percentages run 0-100, well outside the +/-1 range a
               # correlation lives in, so the axis must follow the data.
               xlim = None
           return {
               "type": "single",
               "values": panel_values,
               "whiskers": self._bootstrap_ci(input_samples, values, method),
               "title": title,
               "xlabel": xlabel,
               "xlim": xlim,
           }

       if method == METHOD_SOBOL:
           s1, st, n_dropped, n_groups = self._compute_sobol(
               var_info, self.solved_expr, symbol_order, output_unit
           )
           panel = {
               "type": "grouped",
               "s1": s1,
               "st": st,
               "title": "Sobol Indices (S1 = main effect, ST = total effect)",
               "xlabel": "Sobol index",
           }
           if n_dropped:
               panel["footnote"] = (
                   f"Sobol: the output was undefined (NaN) in {n_dropped:,} of "
                   f"{n_groups:,} sample groups, which were left out. The "
                   f"indices describe only the part of the input range where "
                   f"the function is defined."
               )
           return panel

       if method == METHOD_OAT:
           return {
               "type": "tornado",
               "results": oat_results,
               "title": f"OAT Tornado (nominal = {oat_baseline:.4g})",
               "xlabel": f"\u0394{output_var} from nominal ({output_unit_str})",
           }

       if method == METHOD_ELASTICITY:
           elasticity, spans, notes, summary = self._compute_elasticity(
               var_info, self.solved_expr, symbol_order, output_unit
           )
           return {
               "type": "single",
               "values": elasticity,
               # Deterministic, like the OAT sweep -- it never touches the
               # sampled draw, so there is nothing to bootstrap. The
               # whiskers are a swept range, not a confidence interval: a
               # bar with none is exact everywhere, and a wide one is only
               # good near Nominal.
               "whiskers": spans,
               "title": f"Elasticity at nominal \u2014 {summary}",
               "xlabel": f"% change in {output_var} per +1% change in input",
               # Unbounded: a fourth-power lever would be clipped clean off
               # the default +/-1 axis.
               "xlim": None,
               "notes": notes,
               "footnote": (
                   "Whiskers span each input's own limits. Every elasticity is "
                   "measured with the other variables held at Nominal, so this "
                   "is not the spread across the full operating envelope \u2014 "
                   "an input's elasticity can move further than shown once the "
                   "others are off Nominal too."
               ),
           }

       raise RuntimeError(f"Unknown sensitivity method: {method}")

   # ------------------------------------------------------------------
   # Simulation core — no widgets, no popups. Raises CalculationError.
   # ------------------------------------------------------------------
   def _simulate(self, solved_expr, var_info, output_unit_str, n_iter, seed):
       """Sample every input and evaluate the expression.

       Returns a dict with symbol_order, output_unit, input_samples and
       the raw output `values`, which may still contain NaN — the caller
       decides what to do about those. Raises CalculationError if no
       sample at all produced a finite result."""
       output_unit = ureg(output_unit_str).units
       rng = np.random.default_rng(seed)

       symbol_order = []
       quantities = []
       input_samples = {}

       for info in var_info:
           var = info["var"]
           try:
               samples = self._sample_distribution(
                   rng, info["dist"], info["nominal"], info["lower"],
                   info["upper"], n_iter, cp=info["cp"]
               )
           except ValueError as exc:
               raise CalculationError(
                   "Sampling Error",
                   f"Could not sample variable {var} ({info['dist']}):\n{exc}"
               )

           input_samples[var] = samples
           quantities.append(to_calculation_units(ureg.Quantity(samples, info["unit"])))
           symbol_order.append(sympy.Symbol(var))

       try:
           func = compile_expression(solved_expr, symbol_order)
           result = func(*quantities)
       except pint.DimensionalityError as exc:
           raise CalculationError(
               "Invalid Unit",
               f"Units invalid — unable to calculate.\n\nDetails: {exc}"
           )
       except pint.OffsetUnitCalculusError as exc:
           raise CalculationError(
               "Invalid Unit",
               "Units invalid — unable to calculate.\n\n"
               "One of your variables uses an offset unit (e.g. degC or degF) "
               "in a multiplication/division. Use an absolute-scale unit "
               "instead (e.g. Kelvin instead of Celsius).\n\n"
               f"Details: {exc}"
           )
       except Exception as exc:
           raise CalculationError(
               "Calculation Error", f"Could not evaluate expression:\n{exc}"
           )

       try:
           result_converted = result.to(output_unit)
       except pint.DimensionalityError as exc:
           raise CalculationError(
               "Invalid Unit",
               f"Units invalid — unable to calculate.\n\n"
               f"The computed result has units of '{result.units}', which cannot "
               f"be converted to '{output_unit}'.\n\nDetails: {exc}"
           )

       values = np.asarray(result_converted.magnitude, dtype=float)

       if not np.isfinite(values).any():
           raise CalculationError(
               "Invalid Results",
               "Every sampled result was undefined (NaN). The usual causes "
               "are:\n\n"
               "\u2022 a square root or log of a negative number \u2014 often two "
               "variables' tolerance ranges are physically inconsistent for "
               "this equation (a difference that should be positive comes "
               "out negative across the whole sampled range). Check the "
               "Upper/Lower/Nominal values feeding any sqrt() or log() term.\n"
               "\u2022 a Piecewise whose conditions don't cover the sampled "
               "values \u2014 end it with a (formula, True) branch."
           )

       return {
           "symbol_order": symbol_order,
           "output_unit": output_unit,
           "input_samples": input_samples,
           "values": values,
       }

   def _summarize(self, solved_expr, var_info, symbol_order, output_unit,
                  values, eval_min, eval_max):
       """Deterministic checks plus the statistics both reports share.
       `values` must already be finite-only."""
       try:
           oat_baseline, oat_results = self._compute_oat_sensitivity(
               var_info, solved_expr, symbol_order, output_unit
           )
       except Exception as exc:
           raise CalculationError(
               "Sensitivity Error", f"Could not compute OAT sweep:\n{exc}"
           )

       cp_map = {info["var"]: info["cp"] for info in var_info}
       rss_sigma = self._compute_rss(oat_results, cp_map)
       # RSS is centered on the all-nominal baseline, so the +/-3 sigma
       # window is the statistical stack-up range to compare against the
       # (always wider) worst-case stack-up.
       rss_min = oat_baseline - RSS_SIGMA_MULTIPLIER * rss_sigma
       rss_max = oat_baseline + RSS_SIGMA_MULTIPLIER * rss_sigma

       worst_min, worst_max, worst_case_mode = self._compute_worst_case(
           var_info, solved_expr, symbol_order, output_unit, oat_baseline, oat_results
       )

       std = float(np.std(values))
       mean = float(np.mean(values))
       cp = cpk = cpu = cpl = None
       if std > 0:
           if eval_min is not None and eval_max is not None:
               cp = (eval_max - eval_min) / (6 * std)
           if eval_max is not None:
               cpu = (eval_max - mean) / (3 * std)
           if eval_min is not None:
               cpl = (mean - eval_min) / (3 * std)
           candidates = [v for v in (cpu, cpl) if v is not None]
           if candidates:
               cpk = min(candidates)

       return {
           "oat_baseline": oat_baseline, "oat_results": oat_results,
           "rss_sigma": rss_sigma, "rss_min": rss_min, "rss_max": rss_max,
           "worst_min": worst_min, "worst_max": worst_max,
           "worst_case_mode": worst_case_mode,
           "mean": mean, "std": std, "cp": cp, "cpk": cpk,
       }

   # ------------------------------------------------------------------
   # Process — run Monte Carlo simulation on the rendered slot
   # ------------------------------------------------------------------
   def run_process(self):
       self._sync_master_var_data()
       self._sync_master_output_data()

       try:
           eval_min, eval_max = parse_eval_limits(
               self.eval_min_input.text(), self.eval_max_input.text()
           )
       except ValueError as exc:
           QMessageBox.critical(self, "Invalid Input", str(exc))
           return

       output_var = self._output_var_name()
       output_unit_str = self._output_unit_text().strip()
       n_iter, seed = parse_run_settings(self.iterations_input.text(), self.seed_input.text())

       var_info = build_var_info(self._used_variables, self.master_var_data)

       try:
           sim = self._simulate(self.solved_expr, var_info, output_unit_str, n_iter, seed)
       except CalculationError as exc:
           QMessageBox.critical(self, exc.title, exc.message)
           return

       values = sim["values"]
       finite_mask = np.isfinite(values)
       n_total = len(values)
       n_invalid = int(n_total - finite_mask.sum())

       if n_invalid > 0:
           reply = QMessageBox.warning(
               self, "Some Invalid Results",
               f"{n_invalid:,} of {n_total:,} samples ({100*n_invalid/n_total:.2f}%) "
               "produced an undefined result (NaN) — likely a sqrt() or "
               "log() of a negative number, or a value that falls in a gap "
               "between a Piecewise's conditions, for those particular "
               "sampled combinations.\n\n"
               "These samples will be excluded from the plot and statistics "
               "below. Continue?",
               QMessageBox.Yes | QMessageBox.No
           )
           if reply != QMessageBox.Yes:
               return

       # Every sample-based sensitivity method pairs input sample i with
       # output sample i, so an excluded output must take its inputs with
       # it -- otherwise the arrays no longer line up.
       values = values[finite_mask]
       input_samples = {var: samples[finite_mask]
                        for var, samples in sim["input_samples"].items()}
       symbol_order = sim["symbol_order"]
       output_unit = sim["output_unit"]

       try:
           summary = self._summarize(
               self.solved_expr, var_info, symbol_order, output_unit,
               values, eval_min, eval_max
           )
       except CalculationError as exc:
           QMessageBox.critical(self, exc.title, exc.message)
           return

       oat_baseline = summary["oat_baseline"]
       oat_results = summary["oat_results"]

       method = self.sensitivity_method
       try:
           sensitivity_panel = self._build_sensitivity_panel(
               method, var_info, symbol_order, output_unit, output_var,
               output_unit_str, input_samples, values, oat_baseline,
               oat_results
           )
       except Exception as exc:
           QMessageBox.critical(self, "Sensitivity Error", f"Could not compute {method}:\n{exc}")
           return

       report_data = {
           "output_var": output_var, "output_unit_str": output_unit_str,
           "values": values, "eval_min": eval_min, "eval_max": eval_max,
           "n_invalid": n_invalid,
           "method": method, "sensitivity_panel": sensitivity_panel,
           "output_name": self.master_output_data.get(output_var, {}).get("name", "").strip(),
           "function_desc": self.function_desc_input.text().strip(),
           "oat_baseline": oat_baseline, "rss_sigma": summary["rss_sigma"],
           "rss_min": summary["rss_min"], "rss_max": summary["rss_max"],
           "worst_min": summary["worst_min"], "worst_max": summary["worst_max"],
           "worst_case_mode": summary["worst_case_mode"],
           "cp": summary["cp"], "cpk": summary["cpk"], "seed": seed, "n_iter": n_iter,
           # Frozen with the run, like chart_style, so a reopened report
           # and a later PDF show the numbers as they were run.
           "report_decimals": self.report_decimals,
           # Frozen with the run so a reopened report and a PDF exported
           # later look like the run that produced them, and so the
           # Settings note ("takes effect on the next Process click") is
           # true of the chart as well as of the numbers.
           "chart_style": self.chart_style,
           "function_text": self._solved_function_display_text(),
           # Kept so the tolerance-allocation dialog can run off the last
           # Process without re-sampling.
           "var_info": var_info, "oat_results": oat_results,
       }
       self._last_report_data = report_data
       self._results_signature = self._current_input_signature()
       self._update_results_availability()
       self._show_montecarlo_results(report_data)

   # ------------------------------------------------------------------
   # Distribution chart drawing (histogram / relative / standardized / CDF)
   # ------------------------------------------------------------------
   def _draw_distribution_chart(self, ax1, chart_style, output_var,
                                output_unit_str, values, eval_min, eval_max):
       n = len(values)
       mean = float(np.mean(values))
       std = float(np.std(values))

       # Every style plots the same numbers on the same x axis except the
       # standardized one, which rescales to z. The eval lines have to
       # follow whatever x that turns out to be, so the plotted data and
       # the two reference values are decided together, then drawn once.
       x_values = values
       x_label = f"{output_var} ({output_unit_str})"
       v_min, v_max = eval_min, eval_max

       if chart_style == "Standardized Relative Histogram":
           x_values = (values - mean) / std if std > 0 else np.zeros_like(values)
           x_label = f"{output_var} (Z-Score)"
           # Eval Min/Max are in real units — convert to z-score so the
           # reference lines stay meaningful on this axis.
           v_min = (eval_min - mean) / std if (eval_min is not None and std > 0) else None
           v_max = (eval_max - mean) / std if (eval_max is not None and std > 0) else None
           title = f"Standardized Distribution of {output_var}"
       elif chart_style == "Relative Frequency Histogram":
           title = f"Relative Frequency of {output_var}"
       elif chart_style == "CDF":
           title = f"CDF of {output_var}"
       else:  # "Histogram" (default)
           title = f"Distribution of {output_var}"

       ax1.set_xlabel(x_label)
       ax1.set_title(f"{title}  (n = {n:,})")

       if chart_style == "CDF":
           ax1.plot(np.sort(x_values), np.arange(1, n + 1) / n * 100.0,
                    color="#4C72B0", linewidth=1.5)
           ax1.set_ylabel("Cumulative %")
           ax1.set_ylim(0, 100)

           # Annotate the cumulative % directly at each eval line so the
           # failure fraction is readable without eyeballing the curve.
           if v_min is not None:
               pct_below = float(np.sum(values < eval_min)) / n * 100.0
               ax1.annotate(f"{pct_below:.2f}% below", xy=(v_min, pct_below),
                             xytext=(5, 8), textcoords="offset points", fontsize=7)
           if v_max is not None:
               pct_above = float(np.sum(values > eval_max)) / n * 100.0
               ax1.annotate(f"{pct_above:.2f}% above", xy=(v_max, 100 - pct_above),
                             xytext=(5, -12), textcoords="offset points", fontsize=7)
       else:
           relative = chart_style in RELATIVE_CHART_STYLES
           weights = np.ones_like(x_values) / n if relative else None
           ax1.hist(x_values, bins=50, weights=weights, color="#4C72B0",
                    edgecolor="black", alpha=0.85)
           if relative:
               ax1.set_ylabel("Percentage of Samples")
               ax1.yaxis.set_major_formatter(lambda y, _: f"{y*100:.0f}%")
           else:
               ax1.set_ylabel("Frequency")

       for label, value in (("Eval Min", v_min), ("Eval Max", v_max)):
           if value is not None:
               ax1.axvline(value, color="red", linestyle="--", linewidth=2,
                           label=f"{label} = {value:g}")
       if v_min is not None or v_max is not None:
           ax1.legend(fontsize=8)

   # ------------------------------------------------------------------
   # Build the two-panel figure (shared by dialog display and PDF export)
   # ------------------------------------------------------------------
   def _build_results_figure(self, report_data, figsize=(12, 5.2)):
       output_var = report_data["output_var"]
       output_unit_str = report_data["output_unit_str"]
       values = report_data["values"]
       eval_min = report_data["eval_min"]
       eval_max = report_data["eval_max"]
       sensitivity_panel = report_data["sensitivity_panel"]

       fig = Figure(figsize=figsize)

       ax1 = fig.add_subplot(121)
       self._draw_distribution_chart(
           ax1, report_data.get("chart_style", DEFAULT_CHART_STYLE),
           output_var, output_unit_str, values, eval_min, eval_max
       )

       ax2 = fig.add_subplot(122)

       if sensitivity_panel["type"] == "single":
           items = sorted(sensitivity_panel["values"].items(), key=lambda kv: abs(kv[1]))
           svars = [k for k, v in items]
           svals = [v for k, v in items]
           scolors = ["#c62828" if v < 0 else "#2e7d32" for v in svals]
           ax2.barh(svars, svals, color=scolors, edgecolor="black")

           # Whiskers mean different things per method -- a bootstrap
           # confidence interval for the sampled ones, the span across the
           # input's own limits for elasticity -- so the key is neutral and
           # the panel title says which is being drawn.
           whiskers = sensitivity_panel.get("whiskers")
           if whiskers:
               xerr_lo = [max(0.0, v - whiskers[k][0]) for k, v in items]
               xerr_hi = [max(0.0, whiskers[k][1] - v) for k, v in items]
               ax2.errorbar(svals, svars, xerr=[xerr_lo, xerr_hi],
                            fmt="none", ecolor="black", elinewidth=1, capsize=3)

           ax2.axvline(0, color="black", linewidth=0.8)
           # A method whose values are unbounded passes xlim=None and gets
           # an axis fitted to the data instead of being clipped.
           xlim = sensitivity_panel.get("xlim", SENSITIVITY_XLIM)
           if xlim is None:
               reach = [abs(v) for v in svals] or [1.0]
               if whiskers:
                   reach += [abs(b) for k, _ in items for b in whiskers[k]]
               span = max(reach) * 1.15 or 1.0
               ax2.set_xlim(-span, span)
           else:
               ax2.set_xlim(*xlim)
           ax2.set_xlabel(sensitivity_panel["xlabel"])
           ax2.set_title(sensitivity_panel["title"], fontsize=9)

       elif sensitivity_panel["type"] == "grouped":
           items = sorted(sensitivity_panel["s1"].items(),
                           key=lambda kv: sensitivity_panel["st"][kv[0]])
           svars = [k for k, v in items]
           s1_vals = [sensitivity_panel["s1"][v] for v in svars]
           st_vals = [sensitivity_panel["st"][v] for v in svars]
           y_pos = np.arange(len(svars))
           bar_h = 0.35
           ax2.barh(y_pos - bar_h / 2, s1_vals, height=bar_h, color="#4C72B0",
                    edgecolor="black", label="S1 (main effect)")
           ax2.barh(y_pos + bar_h / 2, st_vals, height=bar_h, color="#DD8452",
                    edgecolor="black", label="ST (total effect)")
           ax2.set_yticks(y_pos)
           ax2.set_yticklabels(svars)
           ax2.axvline(0, color="black", linewidth=0.8)
           ax2.legend(fontsize=8)
           ax2.set_xlabel(sensitivity_panel["xlabel"])
           ax2.set_title(sensitivity_panel["title"], fontsize=9)

       elif sensitivity_panel["type"] == "tornado":
           oat_results = sensitivity_panel["results"]
           oat_sorted = sorted(oat_results.items(), key=lambda kv: abs(kv[1][1] - kv[1][0]))
           oat_vars = [k for k, v in oat_sorted]
           oat_los = [v[0] for k, v in oat_sorted]
           oat_his = [v[1] for k, v in oat_sorted]

           all_edges = oat_los + oat_his + [0.0]
           data_min = min(all_edges)
           data_max = max(all_edges)
           span = data_max - data_min if data_max > data_min else 1.0
           pad = span * 0.22

           for y, (var, lo, hi) in enumerate(zip(oat_vars, oat_los, oat_his)):
               left_val = min(lo, hi)
               right_val = max(lo, hi)
               width = right_val - left_val
               ax2.barh(y, width, left=left_val, color="#7986CB", edgecolor="black")
               ax2.text(left_val - span * 0.02, y, f"{left_val:+.3g}",
                         va="center", ha="right", fontsize=7)
               ax2.text(right_val + span * 0.02, y, f"{right_val:+.3g}",
                         va="center", ha="left", fontsize=7)

           ax2.set_yticks(range(len(oat_vars)))
           ax2.set_yticklabels(oat_vars)
           ax2.axvline(0, color="black", linewidth=0.8)
           ax2.set_xlim(data_min - pad, data_max + pad)
           ax2.set_xlabel(sensitivity_panel["xlabel"])
           ax2.set_title(sensitivity_panel["title"], fontsize=9)

       fig.subplots_adjust(left=0.09, right=0.97, top=0.90, bottom=0.16, wspace=0.35)
       return fig

   # ------------------------------------------------------------------
   # Report text (shared by the results dialog and the PDF export)
   #
   # The solved expression as plain text for the report — the same
   # expression the Monte Carlo run actually evaluated, with all
   # subfunctions substituted in, styled to match the on-screen render.
   # ------------------------------------------------------------------
   def _solved_function_display_text(self):
       if self.solved_expr is None:
           return self.function_input.text()
       lhs_name = lhs_name_of(self.function_input.text().strip())
       return solved_function_text(lhs_name, self.solved_expr, self.render_style)

   def _build_stats_text(self, report_data):
       output_unit_str = report_data["output_unit_str"]
       values = report_data["values"]
       eval_min = report_data["eval_min"]
       eval_max = report_data["eval_max"]
       n = len(values)
       decimals = report_data["report_decimals"]

       def num(value):
           return format_report_number(value, decimals)

       fail = eval_failure_stats(values, eval_min, eval_max)
       below, above = fail["below"], fail["above"]
       pct_below, pct_above = fail["pct_below"], fail["pct_above"]
       pct_total_fail = fail["pct_total_fail"]

       seed_display = report_data["seed"] if report_data["seed"] is not None else "(random, not fixed)"

       # Substituting subfunctions can make this expression far wider than
       # the page, and matplotlib's text drawing does not wrap, so wrap it
       # here with continuation lines aligned under the first one.
       function_line = textwrap.fill(
           f"Function: {report_data['function_text']}",
           width=STATS_WRAP_WIDTH,
           subsequent_indent=" " * len("Function: "),
           break_long_words=True,
           break_on_hyphens=False,
       )

       output_name = report_data["output_name"] or "\u2014"
       output_line = f"Output:      {output_name}  [{output_unit_str}]"
       description = report_data["function_desc"] or "\u2014"
       description_line = textwrap.fill(
           f"Description: {description}",
           width=STATS_WRAP_WIDTH,
           subsequent_indent=" " * len("Description: "),
       )

       text = (
           f"{output_line}\n"
           f"{description_line}\n"
           f"{function_line}\n"
           f"\n{_stats_heading('MONTE CARLO RESULTS')}\n"
           f"Iterations: {report_data['n_iter']:,}    Seed: {seed_display}\n\n"
           f"Mean:    {num(np.mean(values))} {output_unit_str}\n"
           f"Std Dev: {num(np.std(values))} {output_unit_str}\n"
           f"Min:     {num(np.min(values))} {output_unit_str}\n"
           f"Max:     {num(np.max(values))} {output_unit_str}\n"
       )
       # Undefined samples were dropped before every statistic above, so
       # say how many: the figures describe the remaining samples only.
       n_invalid = report_data["n_invalid"]
       if n_invalid:
           text += (f"Invalid: {n_invalid:,} of {report_data['n_iter']:,} samples "
                    f"({num(100.0 * n_invalid / report_data['n_iter'])}%) undefined "
                    f"(NaN), excluded from these results\n")
       else:
           text += "Invalid: 0\n"

       if eval_min is not None:
           text += f"\nFailed Eval Min: {num(pct_below)}%  ({below:,} / {n:,})"
       if eval_max is not None:
           text += f"\nFailed Eval Max: {num(pct_above)}%  ({above:,} / {n:,})"
       if pct_total_fail is not None:
           text += f"\nTotal Failure:   {num(pct_total_fail)}%"

       if report_data["cp"] is not None:
           text += f"\n\nCp:  {num(report_data['cp'])}"
       if report_data["cpk"] is not None:
           text += f"\nCpk: {num(report_data['cpk'])}"

       # Monte Carlo estimates are themselves uncertain: run again with a
       # different seed and they move. Raw standard errors leave the reader
       # to work out what to do about them, so this states a verdict and a
       # concrete iteration count instead.
       text += f"\n\n{_stats_heading('CONVERGENCE (is the iteration count enough?)')}\n"
       text += self._convergence_text(values, n, output_unit_str, pct_total_fail, decimals)

       # Everything below is computed from the tolerance limits, not from
       # the sampled values, so it does not change with the iteration count
       # or the seed. Separated from the Monte Carlo block so the two are
       # not read as one set of numbers.
       text += f"\n\n{_stats_heading('DETERMINISTIC ANALYSIS')}\n"
       text += "(from the tolerance limits; independent of iterations and seed)\n"
       text += (
           f"\nOAT Baseline (all-nominal): {num(report_data['oat_baseline'])} {output_unit_str}"
       )
       sigma_mult = format_number(RSS_SIGMA_MULTIPLIER)
       text += (
           f"\nRSS estimate (linear approx): "
           f"\u03c3 \u2248 {num(report_data['rss_sigma'])} {output_unit_str}"
       )
       text += (
           f"\nRSS range at \u00b1{sigma_mult}\u03c3: "
           f"[{num(report_data['rss_min'])}, {num(report_data['rss_max'])}] {output_unit_str}"
       )

       worst_case_label = WORST_CASE_MODE_LABELS.get(
           report_data["worst_case_mode"], report_data["worst_case_mode"]
       )
       text += (
           f"\nWorst-case stack-up ({worst_case_label}): "
           f"[{num(report_data['worst_min'])}, {num(report_data['worst_max'])}] {output_unit_str}"
       )

       text += f"\n\n{_stats_heading('SENSITIVITY')}\n"
       text += f"Sensitivity Analysis Method: {report_data['method']}"

       # A method may attach per-variable caveats -- which variables were
       # suppressed and why, or how far a number can be trusted. Printed
       # here because the chart has no room for them, and a bare bar with
       # no explanation of a missing variable is worse than no bar.
       panel = report_data.get("sensitivity_panel") or {}
       if panel.get("footnote"):
           text += "\n" + textwrap.fill(panel["footnote"],
                                        width=STATS_WRAP_WIDTH)
       for var, note in (panel.get("notes") or {}).items():
           text += f"\n  {var}: {note}"

       return text

   # ------------------------------------------------------------------
   # Results dialog
   # ------------------------------------------------------------------
   def _show_montecarlo_results(self, report_data):
       dialog = QDialog(self)
       title = f"Monte Carlo Results — {report_data['output_var']}"
       if report_data["output_name"]:
           title += f" ({report_data['output_name']})"
       dialog.setWindowTitle(title)

       # Never taller than the screen can show. A hardcoded height pushes
       # the buttons off the bottom on a smaller display, and the stats
       # block grows as sections are added to the report.
       available = QApplication.primaryScreen().availableGeometry()
       dialog.resize(min(RESULTS_DIALOG_WIDTH, available.width() - 80),
                    min(RESULTS_DIALOG_HEIGHT, available.height() - 80))
       layout = QVBoxLayout(dialog)

       # Chart and stats scroll together; the buttons stay pinned below so
       # Export and Close are always reachable no matter how long the
       # report gets.
       scroll = QScrollArea()
       scroll.setWidgetResizable(True)
       scroll.setFrameShape(QFrame.NoFrame)
       scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
       scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

       content = QWidget()
       content_layout = QVBoxLayout(content)
       content_layout.setContentsMargins(0, 0, 0, 0)

       fig = self._build_results_figure(report_data)
       canvas = FigureCanvas(fig)
       canvas.setMinimumHeight(RESULTS_CHART_HEIGHT)
       content_layout.addWidget(canvas)

       stats_label = QLabel(self._build_stats_text(report_data))
       stats_label.setFont(QFont("Courier New", 10))
       stats_label.setWordWrap(True)
       # Selectable so figures can be copied straight out of the report.
       stats_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
       content_layout.addWidget(stats_label)
       content_layout.addStretch(1)

       scroll.setWidget(content)
       layout.addWidget(scroll, 1)

       btn_row = QHBoxLayout()
       btn_row.addWidget(text_button(
           "Export Report (PDF)",
           lambda: self._export_pdf_report(report_data)
       ))
       btn_row.addStretch(1)
       btn_row.addWidget(text_button("Close", dialog.accept))
       layout.addLayout(btn_row)

       dialog.exec_()

   # ------------------------------------------------------------------
   # PDF export
   # ------------------------------------------------------------------
   def _export_pdf_report(self, report_data):
       path, _ = QFileDialog.getSaveFileName(
           self, "Export Report", "", "PDF Files (*.pdf)"
       )
       if not path:
           return
       if not path.lower().endswith(".pdf"):
           path += ".pdf"

       try:
           with PdfPages(path) as pdf:
               fig1 = self._build_results_figure(report_data, figsize=(11, 5.5))
               pdf.savefig(fig1)

               fig2 = Figure(figsize=(8.5, 11))
               ax = fig2.add_subplot(111)
               ax.axis("off")
               header = (
                   f"Monte Carlo Tolerance Analysis Report\n"
                   f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
               )
               ax.text(0.02, 0.98, header + self._build_stats_text(report_data),
                       va="top", ha="left", fontsize=9, family="monospace",
                       transform=ax.transAxes)
               pdf.savefig(fig2)

           QMessageBox.information(self, "Exported", f"Report saved to:\n{path}")
       except Exception as exc:
           QMessageBox.critical(self, "Export Error", str(exc))


   # ------------------------------------------------------------------
   # Process All report — table cells shared by dialog and PDF
   # ------------------------------------------------------------------
   def _process_all_cells(self, row, report):
       """The row's cells as (text, colour) pairs, in column order.
       colour is None for normal text, or the pass/fail colour for an
       output value judged against the row's Eval limits."""
       decimals = report["report_decimals"]
       eval_min, eval_max = row["eval_min"], row["eval_max"]
       has_limits = eval_min is not None or eval_max is not None

       def num(value):
           if value is None:
               return "\u2014"
           return format_report_number(value, decimals)

       def plain(text):
           return (text, None)

       def judged(value, suffix=""):
           """An output value, coloured by whether it is inside the row's
           Eval limits. A value equal to a limit is inside, matching how
           Fail % counts."""
           if not has_limits:
               return (num(value) + suffix, None)
           inside = ((eval_min is None or value >= eval_min)
                     and (eval_max is None or value <= eval_max))
           return (num(value) + suffix, EVAL_PASS_COLOR if inside else EVAL_FAIL_COLOR)

       approx = "*" if row["worst_case_mode"] == WORST_CASE_APPROXIMATE else ""
       seed = "random" if row["seed"] is None else str(row["seed"])
       by_group = {
           "Function": [
               plain(str(row["slot"])),
               plain(row["output_var"]),
               plain(row["output_name"] or "\u2014"),
               plain(row["output_unit_str"]),
           ],
           "Monte Carlo": [
               plain(f"{row['n_iter']:,}"),
               plain(seed),
               judged(row["mean"]),
               judged(row["min"]),
               judged(row["max"]),
           ],
           "Worst Case": [
               judged(row["worst_min"], approx),
               judged(row["worst_max"], approx),
           ],
           RSS_RANGE_GROUP: [
               judged(row["rss_min"]),
               judged(row["rss_max"]),
           ],
           EVAL_LIMITS_GROUP: [
               plain(num(row["eval_min"])),
               plain(num(row["eval_max"])),
               plain(num(row["pct_total_fail"])),
               plain(num(row["cp"])),
               plain(num(row["cpk"])),
           ],
       }
       return [cell for group in report["layout"]["groups"] for cell in by_group[group]]

   def _process_all_header_text(self, report):
       return f"Functions processed: {len(report['rows'])}"

   def _process_all_footnotes(self, report):
       notes = []
       if any(row["eval_min"] is not None or row["eval_max"] is not None
              for row in report["rows"]):
           notes.append(
               f"Mean, Min, Max, Worst Case and {RSS_RANGE_GROUP} are green "
               f"inside that row's Eval limits and red outside them (a value "
               f"equal to a limit counts as inside)."
           )
       if any(row["worst_case_mode"] == WORST_CASE_APPROXIMATE for row in report["rows"]):
           notes.append(
               f"* Worst case is approximate (independent-swing) for functions "
               f"with more than {WORST_CASE_EXACT_LIMIT} varying variables."
           )
       invalid = [f"slot {row['slot']} ({row['n_invalid']:,} of {row['n_iter']:,})"
                  for row in report["rows"] if row["n_invalid"]]
       if invalid:
           notes.append(
               "Samples with an undefined result (NaN) were excluded from "
               "the statistics: " + ", ".join(invalid) + "."
           )
       return notes

   def _not_processed_lines(self, report):
       lines = []
       for item in report["not_processed"]:
           lines.append(f"{item['label']}:")
           for reason in item["reasons"]:
               lines.append(textwrap.fill(
                   " ".join(reason.split()),
                   width=STATS_WRAP_WIDTH,
                   initial_indent="   \u2022 ",
                   subsequent_indent="     ",
               ))
       return lines

   def _show_process_all_results(self, report):
       dialog = QDialog(self)
       dialog.setWindowTitle("Process All \u2014 Results")
       # Never larger than the screen can show, like the Process report.
       available = QApplication.primaryScreen().availableGeometry()
       dialog.resize(min(1400, available.width() - 80),
                     min(650, available.height() - 80))
       layout = QVBoxLayout(dialog)

       layout.addWidget(bold_label(self._process_all_header_text(report), SIM_LABEL_POINT_SIZE))

       layout_spec = report["layout"]
       table = QTableWidget(len(report["rows"]), len(layout_spec["headers"]))
       table.setHorizontalHeader(GroupedHeaderView(layout_spec["spans"], table))
       table.setHorizontalHeaderLabels(layout_spec["headers"])
       table.verticalHeader().setVisible(False)
       table.setEditTriggers(QTableWidget.NoEditTriggers)
       for r, row in enumerate(report["rows"]):
           for c, (text, colour) in enumerate(self._process_all_cells(row, report)):
               item = QTableWidgetItem(text)
               item.setTextAlignment(Qt.AlignCenter)
               if colour:
                   item.setForeground(QColor(colour))
               table.setItem(r, c, item)
       table.resizeColumnsToContents()
       layout.addWidget(table, stretch=1)

       # The Input Variables table is part of the exported PDF only;
       # this window shows the results.

       notes = self._process_all_footnotes(report)
       if notes:
           layout.addWidget(note_label("<br>".join(notes)))

       if report["not_processed"]:
           layout.addWidget(bold_label("Not processed:", SIM_LABEL_POINT_SIZE))
           skipped_text = QTextEdit()
           skipped_text.setReadOnly(True)
           skipped_text.setFont(QFont("Courier New", 9))
           skipped_text.setPlainText("\n".join(self._not_processed_lines(report)))
           skipped_text.setMaximumHeight(140)
           layout.addWidget(skipped_text)

       btn_row = QHBoxLayout()
       export_btn = text_button(
           "Export Report (PDF)", lambda: self._export_process_all_pdf(report)
       )
       # The PDF holds only the results table, so with no results there
       # is nothing to export.
       export_btn.setEnabled(bool(report["rows"]))
       btn_row.addWidget(export_btn)
       btn_row.addStretch(1)
       btn_row.addWidget(text_button("Close", dialog.accept))
       layout.addLayout(btn_row)

       dialog.exec_()

   def _process_all_pdf_pages(self, report):
       """Split rows into landscape pages, counting wrapped lines so a
       page never overflows however long the names and descriptions are.
       Returns a list of pages, each a list of (cells, n_lines), where
       cells are (text, colour) pairs."""
       capacity = 1.0 / PDF_LINE_HEIGHT - PDF_HEADER_LINES - PDF_GROUP_HEADER_LINES
       # The Function group always comes first, so its labels are unique
       # positions even though "Min"/"Max" repeat in later groups.
       headers = report["layout"]["headers"]
       wrap_columns = [(headers.index("Var Name"), PDF_NAME_WRAP),
                       (headers.index("Unit"), PDF_UNIT_WRAP)]
       pages = [[]]
       used = 0.0
       for row in report["rows"]:
           cells = list(self._process_all_cells(row, report))
           for index, width in wrap_columns:
               text, colour = cells[index]
               cells[index] = (textwrap.fill(text, width, break_long_words=True), colour)
           n_lines = max(text.count("\n") + 1 for text, _ in cells)
           row_units = n_lines + PDF_ROW_PADDING
           if pages[-1] and used + row_units > capacity:
               pages.append([])
               used = 0.0
           pages[-1].append((cells, n_lines))
           used += row_units
       return pages

   def _export_process_all_pdf(self, report):
       path, _ = QFileDialog.getSaveFileName(
           self, "Export Report", "", "PDF Files (*.pdf)"
       )
       if not path:
           return
       if not path.lower().endswith(".pdf"):
           path += ".pdf"

       try:
           self._write_process_all_pdf(report, path)
           QMessageBox.information(self, "Exported", f"Report saved to:\n{path}")
       except Exception as exc:
           QMessageBox.critical(self, "Export Error", str(exc))

   def _draw_process_all_pdf_table(self, ax, page, layout, col_widths):
       """Group-label row, column-label row, then the page's data rows.
       The table is pinned to an exact bbox at the top of the axes so the
       group labels can be placed over their spans in axes coordinates."""
       n_cols = len(layout["headers"])
       row_units = (
           [PDF_GROUP_HEADER_LINES, PDF_HEADER_LINES]
           + [n_lines + PDF_ROW_PADDING for _, n_lines in page]
       )
       total_height = PDF_LINE_HEIGHT * sum(row_units)

       cell_text = [[""] * n_cols, list(layout["headers"])]
       cell_text += [[text for text, _ in cells] for cells, _ in page]
       table = ax.table(
           cellText=cell_text, colWidths=col_widths, cellLoc="center",
           bbox=[0, 1 - total_height, 1, total_height],
       )
       table.auto_set_font_size(False)
       table.set_fontsize(6.5)

       for r, units in enumerate(row_units):
           for c in range(n_cols):
               table[r, c].set_height(PDF_LINE_HEIGHT * units)

       for c in range(n_cols):
           label_cell = table[1, c]
           label_cell.set_facecolor("#e0e0e0")
           label_cell.get_text().set_fontweight("bold")
           # The group row's per-column cells are hidden; each group is
           # drawn below as one rectangle spanning its columns.
           table[0, c].set_visible(False)

       for r, (cells, _) in enumerate(page, start=2):
           for c, (_, colour) in enumerate(cells):
               if colour:
                   table[r, c].get_text().set_color(colour)

       # A heavier box around each group, header to last row, so the
       # Monte Carlo, Worst Case, RSS, ... blocks read as separate areas.
       for _, start, count in layout["spans"]:
           ax.add_patch(Rectangle(
               (sum(col_widths[:start]), 1 - total_height),
               sum(col_widths[start:start + count]), total_height,
               transform=ax.transAxes, fill=False, edgecolor="black",
               linewidth=PDF_GROUP_BORDER_WIDTH, zorder=5,
           ))

       group_height = PDF_LINE_HEIGHT * PDF_GROUP_HEADER_LINES
       for label, start, count in layout["spans"]:
           left = sum(col_widths[:start])
           width = sum(col_widths[start:start + count])
           ax.add_patch(Rectangle(
               (left, 1 - group_height), width, group_height,
               transform=ax.transAxes, facecolor="#c8c8c8",
               edgecolor="black", linewidth=table[1, 0].get_linewidth(),
           ))
           ax.text(left + width / 2, 1 - group_height / 2, label,
                   ha="center", va="center", fontsize=7, fontweight="bold",
                   transform=ax.transAxes)

   def _write_process_all_pdf(self, report, path):
       header = (
           "Monte Carlo Tolerance Analysis \u2014 Process All\n"
           f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}    "
           + self._process_all_header_text(report)
       )
       weights = report["layout"]["weights"]
       col_widths = [w / sum(weights) for w in weights]
       notes = self._process_all_footnotes(report)

       pages = self._process_all_pdf_pages(report)
       with PdfPages(path) as pdf:
           for page_number, page in enumerate(pages, start=1):
               fig = Figure(figsize=(11, 8.5))
               ax = fig.add_axes([0.03, 0.05, 0.94, 0.80])
               ax.axis("off")
               fig.text(0.03, 0.95, header, va="top", ha="left", fontsize=9,
                        family="monospace")
               if len(pages) > 1:
                   fig.text(0.97, 0.95, f"Page {page_number} of {len(pages)}",
                            va="top", ha="right", fontsize=8)

               self._draw_process_all_pdf_table(ax, page, report["layout"], col_widths)

               if page_number == len(pages) and notes:
                   fig.text(0.03, 0.03, "\n".join(notes), va="bottom", ha="left",
                            fontsize=7)
               pdf.savefig(fig)

           self._write_process_all_variable_pages(pdf, report)

   def _write_process_all_variable_pages(self, pdf, report):
       """The variables the processed functions use, with their limits --
       the Input Variables table as it stood when Process All ran."""
       if not report["variables"]:
           return
       headers = [name for name, _ in PROCESS_ALL_VARIABLE_COLUMNS]
       weights = [weight for _, weight in PROCESS_ALL_VARIABLE_COLUMNS]
       col_widths = [w / sum(weights) for w in weights]

       rows = []
       for cells in report["variables"]:
           cells = list(cells)
           cells[1] = textwrap.fill(cells[1], PDF_VAR_NAME_WRAP, break_long_words=True)
           rows.append((cells, max(text.count("\n") + 1 for text in cells)))

       capacity = 1.0 / PDF_LINE_HEIGHT - PDF_HEADER_LINES
       pages, used = [[]], 0.0
       for cells, n_lines in rows:
           units = n_lines + PDF_ROW_PADDING
           if pages[-1] and used + units > capacity:
               pages.append([])
               used = 0.0
           pages[-1].append((cells, n_lines))
           used += units

       for page_number, page in enumerate(pages, start=1):
           fig = Figure(figsize=(11, 8.5))
           ax = fig.add_axes([0.03, 0.05, 0.94, 0.80])
           ax.axis("off")
           title = "Input Variables"
           if len(pages) > 1:
               title += f"  (page {page_number} of {len(pages)})"
           fig.text(0.03, 0.95, title, va="top", ha="left", fontsize=11,
                    fontweight="bold")

           row_units = [PDF_HEADER_LINES] + [n + PDF_ROW_PADDING for _, n in page]
           total_height = PDF_LINE_HEIGHT * sum(row_units)
           table = ax.table(
               cellText=[headers] + [cells for cells, _ in page],
               colWidths=col_widths, cellLoc="center",
               bbox=[0, 1 - total_height, 1, total_height],
           )
           table.auto_set_font_size(False)
           table.set_fontsize(7)
           for r, units in enumerate(row_units):
               for c in range(len(headers)):
                   table[r, c].set_height(PDF_LINE_HEIGHT * units)
           for c in range(len(headers)):
               table[0, c].set_facecolor("#e0e0e0")
               table[0, c].get_text().set_fontweight("bold")
           pdf.savefig(fig)


def main():
   app = QApplication(sys.argv)
   window = FunctionVariableTool()
   window.show()
   sys.exit(app.exec_())


if __name__ == "__main__":
   main()
