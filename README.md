# Montecarlo Tool — Function / Variable Tolerance Analysis

A desktop tool for tolerance stack-up analysis. Type an engineering formula, give each variable its tolerance, distribution and unit, and the tool runs a Monte Carlo simulation alongside worst-case and RSS stack-ups, sensitivity analysis and capability (Cp/Cpk) — with full unit checking throughout.

Current version: **1.1.4**

---

## Features

- **Formula entry with live rendering** — functions are typed as plain text (`C = (w - t)/w`) and rendered as equations. Up to 20 function slots per project, plus shared **subfunctions** that are substituted automatically.
- **Units everywhere** — every variable carries a unit (`in`, `mm`, `MPa`, `degree`, `%`, …) via [pint](https://pint.readthedocs.io/). Mismatched units are reported instead of silently producing wrong numbers.
- **Conditional formulas** — `Piecewise((A, x <= 3), (B, True))`, displayed in standard cases notation. Gaps between conditions are detected and reported with the exact uncovered range.
- **Monte Carlo simulation** — Normal, Uniform, Triangular and Lognormal distributions, Fixed values, symmetric or asymmetric tolerances, and a per-variable **Cp** that sets how tightly the process holds its limits.
- **Deterministic analysis** — worst-case stack-up (exact for linear functions or up to 20 varying variables, approximate beyond), RSS estimate with ±3σ range, and all-nominal baseline.
- **Sensitivity analysis** — Pearson correlation, standardized regression (SRC), variance contribution, Sobol indices, OAT tornado and elasticity, with bootstrap confidence intervals where applicable.
- **Specification checks** — Eval Min/Max per function, failure percentage, Cp/Cpk and a convergence check on the iteration count.
- **Tolerance allocation** — work backwards from a required output spec to the tolerance each input can have.
- **Process All** — run every function in a project at once and get one summary table (window and landscape PDF), with values coloured green/red against their Eval limits and an Input Variables appendix.
- **Reports** — distribution charts (histogram, relative/standardized histogram, CDF), sensitivity charts and statistics, exportable to PDF. Numbers are rounded to a configurable number of decimal places.
- **Annotations** — a built-in sheet editor for pasting images and adding text and arrows, saved with the project.
- **Projects** — everything is saved to a single `.mcz` file.

## Requirements

- Python 3.9 or newer (developed and tested on Python 3.12)
- [PyQt5](https://pypi.org/project/PyQt5/)
- [NumPy](https://numpy.org/)
- [SymPy](https://www.sympy.org/)
- [Pint](https://pint.readthedocs.io/)
- [Matplotlib](https://matplotlib.org/)
- [SALib](https://salib.readthedocs.io/) — optional; needed only for Sobol indices

## Installation

```bash
git clone <repository-url>
cd <repository-folder>
pip install PyQt5 numpy sympy pint matplotlib SALib
```

## Running

```bash
python MonteCarlo_Calculator_alt.py
```

`MonteCarlo_Calculator_alt.py`, `help_text.py` and `annotator.py` must be in the same folder.

## Quick start

1. **Enter a function** in the Function field, e.g. `C_conc = (w_r - (d_4 - d_3)/2)/w_r`.
2. Click **Render / Load Table**. The equation is rendered and the Input Variables table is filled with the variables it uses.
3. For each variable, choose a **Tol Type**, enter its limits (and Nominal for asymmetric tolerances), a **Unit** and a **Distribution**.
4. Give the output a **Unit** in the Output Variable table. Optionally set **Eval Min / Eval Max** for a specification check.
5. Click **Process**. The report shows the distribution, statistics, worst case, RSS, Cp/Cpk, convergence and the selected sensitivity analysis.
6. **Save Inputs** writes the whole project to a `.mcz` file.

Switching to a slot that already has a function renders it automatically. After any edit the rendered equation turns light gray and Process is grayed until you render again.

## Writing formulas

| Purpose | Syntax |
|---|---|
| Arithmetic | `+ - * /`, powers with `^` or `**` |
| Functions | `sqrt`, `sin`, `cos`, `tan`, `exp`, `log`, `ln`, `abs` |
| Constants | `pi` |
| Variable names | letters, digits and underscores, e.g. `d_3`, `P_out`, `th_w` |
| Conditional | `Piecewise((formula_A, x <= 3), (formula_B, True))` |
| Subfunction | a separate row such as `w_r = d_2*(1 - Y_s/100)`, used by name in any function |

Conditions use `<`, `<=`, `>`, `>=`. In a Piecewise the first matching branch wins; end with a `True` branch to cover every remaining value. A branch that is just `0` is accepted whatever unit the other branches have.

Function names (`sin`, `pi`, …) and Python keywords are reserved and can't be used as variable names. The built-in help (the **?** icon) has the full syntax guide and an explanation of every result.

## Sensitivity methods

| Method | What it tells you |
|---|---|
| Pearson Correlation | Linear association of each input with the output |
| Standardized Regression (SRC) | Each input's effect from a linear fit, comparable across variables |
| Variance Contribution (%) | Each input's share of the output variance |
| Sobol Indices | Main (S1) and total (ST) effect shares, including interactions; requires SALib |
| OAT Tornado (Deterministic) | Output swing as each input moves alone across its limits |
| Elasticity (% / %) | Percentage change in output per percentage change in each input |

The method is chosen in **Settings** (gear icon). Process All does not run sensitivity analysis.

## Project files (`.mcz`)

A `.mcz` file is a zip archive containing `inputs.json` (functions, subfunctions, variables, outputs, per-slot settings and app settings) plus any annotation images. Only variables used by at least one function or subfunction are saved.

### Example projects

| File | Contents |
|---|---|
| `Radial_Piston_Seal_-_Oring_Selector_1.mcz` | O-ring piston seal: depth + gap, extrusion gap, stretch, compression, gland fill (with wall angle, corner radii, volume swell and up to two back-up rings), eccentric variants and deformation force |
| `Radial_Rod Seal - Oring Selector.mcz` | O-ring rod seal with the same metrics plus interference of the O-ring outer diameter |
| `Radial_Piston Seal - Oring Selector w glyd ring.mcz` | Piston seal with a PTFE step-seal ring energized by an O-ring |
| `Radial_Rod Seal - Oring Selector w glyd ring.mcz` | Rod seal with a PTFE step-seal ring energized by an O-ring |

The O-ring formulas follow the Parker O-Ring Handbook (ORD 5700), including Parker's stretch/cross-section reduction curve. Deformation force uses Lindley's approximation with Gent's hardness-to-modulus relation, so treat it as an estimate. The rod-seal and Glyd Ring projects use illustrative dimensions — check them against manufacturer data before relying on the results.

## Repository layout

```
MonteCarlo_Calculator_alt.py   Main application
help_text.py                   Built-in help content (HTML)
annotator.py                   Annotation sheet editor
*.mcz                          Example projects
README.md
```

## Building a standalone executable

To run on machines without Python, the app can be bundled with [PyInstaller](https://pyinstaller.org/):

```bash
pip install pyinstaller
pyinstaller --onefile --windowed MonteCarlo_Calculator_alt.py
```

The executable is written to `dist/`. Build on each target operating system separately (a Windows build on Windows, a macOS build on macOS).

## Notes and limitations

- Monte Carlo min/max are sample extremes and normally fall inside the worst-case range. With Normal distributions at Cp = 1, about 0.27% of each variable's samples fall outside its limits, so a few results can lie slightly beyond the worst case.
- The worst case is exact for linear functions and for up to 20 varying variables; beyond that it is an independent-swing approximation, marked `*` in Process All.
- Samples with an undefined result (e.g. the square root of a negative number, or a value in a Piecewise gap) are excluded and counted in the report.

## License

_To be added._
