"""Help dialog content for MonteCarlo_Calculator.

Kept in a module rather than a data file so PyInstaller picks it up by
following the import. A .html file would need --add-data plus
sys._MEIPASS path handling at runtime, and would fail silently in the
frozen build if either were forgotten.
"""

HELP_HTML = """
<h2>Function Entry — Syntax Guide</h2>

<h3>Basic Operators</h3>
<ul>
<li><b>Addition / Subtraction:</b> <code>+</code>  <code>-</code></li>
<li><b>Multiplication:</b> <code>*</code>, or just write terms next to each other
   (<b>implicit multiplication</b>) — <code>28ab^3</code> is automatically read as
   <code>28 * a * b^3</code></li>
<li><b>Division:</b> <code>/</code></li>
<li><b>Exponent / Power:</b> <code>^</code> or <code>**</code> — both work</li>
<li><b>Parentheses:</b> <code>(...)</code> group terms as usual</li>
</ul>

<h3>Naming a Variable</h3>
<p><b>Multi-letter names are split apart unless you use an underscore.</b>
Adjacent letters with no operator between them are read as separate
single-letter variables — <code>ab</code> becomes <code>a * b</code>, and
<code>Flow</code> becomes <code>F * l * o * w</code>. This is what makes
implicit multiplication work, so it applies everywhere.</p>
<ul>
<li><b>Single letters</b> are always safe: <code>V</code>, <code>x</code>,
   <code>Q</code>, <code>T</code></li>
<li><b>For a genuinely multi-letter name, add an underscore:</b>
   <code>x_max</code>, <code>k_1</code>, <code>temp_c</code>,
   <code>t_wall</code>, <code>Re_num</code> — each stays as ONE variable</li>
<li><b>Spelled-out Greek names are the exception</b> and survive intact
   without an underscore (see below)</li>
</ul>
<p>If a variable you expected disappears from the Input Variables table
and several single letters appear instead, this splitting is why.</p>

<h3>Greek Letters</h3>
<p>Spell out the Greek letter's name (<code>rho</code>, <code>theta</code>,
<code>alpha</code>, <code>sigma</code>, <code>phi</code>, <code>Delta</code>,
etc.) and it renders as the actual Greek symbol in LaTeX. These are
recognized as whole names, so unlike other multi-letter words they are
<b>not</b> split into single letters. Typing the literal Unicode
character also works but skips the automatic styling.</p>
<p><b>Exception:</b> <code>lambda</code> is a reserved Python word and
cannot be used on its own — write <code>lambda_1</code> (or any
subscript) instead. The app will tell you at Render time if you hit one
of these.</p>

<h3>Reserved Words</h3>
<p>These are rejected at Render time with a popup naming the exact word:</p>
<ul>
<li><b>Math constants:</b> <code>pi</code></li>
<li><b>Math functions:</b> <code>sin</code>, <code>cos</code>, <code>tan</code>,
   <code>exp</code>, <code>log</code>, <code>ln</code>, <code>sqrt</code>,
   <code>abs</code></li>
<li><b>Python keywords:</b> <code>lambda</code>, <code>if</code>,
   <code>in</code>, <code>is</code>, <code>and</code>, <code>or</code>,
   <code>not</code>, <code>None</code>, <code>class</code>, and the rest.
   <code>True</code> and <code>False</code> are the exception: they are
   values, not names, and are allowed in formulas (see Conditional
   Formulas below).</li>
</ul>
<p>In every case a subscript makes the name legal again —
<code>log_1</code>, <code>lambda_2</code>. Note that only whole words are
reserved: <code>sin</code> is, but <code>sin_1</code> and
<code>a_in_b</code> are fine.</p>
<p>Names like <code>Q</code>, <code>C</code>, <code>E</code>, <code>I</code>,
<code>N</code>, <code>S</code>, <code>O</code>, <code>beta</code> and
<code>gamma</code> are safe to use as ordinary variables — the app
automatically treats them as plain symbols rather than SymPy's internal
objects.</p>

<h3>Conditional Formulas (Piecewise)</h3>
<p>To use a different formula depending on a value, write
<code>Piecewise((formula A, condition), (formula B, True))</code> &mdash;
"use A when the condition holds, otherwise B". For example:</p>
<p><code>Y = Piecewise((1.19*X - 0.19*X^2, X &lt;= 3), (0.56 + 0.59*X, True))</code></p>
<ul>
<li>Add as many <code>(formula, condition)</code> pairs as you need; the
   first one whose condition holds is used, so where two conditions
   overlap the earlier branch wins. End with a <code>True</code> pair as
   the &quot;otherwise&quot; case.</li>
<li>Conditions use <code>&lt;</code>, <code>&lt;=</code>,
   <code>&gt;</code>, <code>&gt;=</code>. The <code>=</code> goes second:
   <code>=&gt;</code> and <code>=&lt;</code> are rejected with a message
   saying so.</li>
<li><b>Gaps.</b> A value that matches no branch is undefined (NaN). If a
   Piecewise doesn't end with <code>True</code> and its conditions leave
   values uncovered, Render still draws it but warns you, naming the
   uncovered range when the conditions use a single variable (for example
   <i>3 &lt; X_s &le; 4</i>). Samples landing in a gap are handled like
   any other invalid result (see Invalid / NaN Results), and a nominal
   value inside the gap leaves the deterministic results undefined too.</li>
<li>Every branch must come out in the same kind of unit (all lengths, or
   all dimensionless); mixing them is a unit error, as anywhere else. A
   branch that is just <code>0</code> is the exception: zero is zero in
   any unit, so <code>Piecewise((0, q &lt;= 0), (F, True))</code> works
   whatever unit F has.</li>
<li>Works in main functions and subfunctions, and is evaluated sample by
   sample, so each Monte Carlo draw takes the branch its own values
   select.</li>
<li>The equation displays show it in standard cases notation: a brace
   with one branch per line, the formulas left-aligned and the conditions
   in a column (<i>if condition</i>, and <i>otherwise</i> for the last
   one). sympy simplifies conditions that earlier branches already rule
   out, so <code>(x &gt;= 0) &amp; (x &lt; 2)</code> after an
   <code>x &lt; 0</code> branch shows as <i>if x &lt; 2</i> &mdash; it
   means the same thing.</li>
</ul>

<h3>Multiple Main Functions (Slots 1–20)</h3>
<p>The number dropdown next to the Function field selects one of 20
function "slots." Type a formula into the Function field (e.g.
<code>y = a + b^2</code>), optionally give it a Function Desc, and
click Render/Update Table — it's saved into the currently selected slot.
Switch slots from the dropdown to work on a different function; the
Input Variables table always shows only the variables belonging to
whichever slot was last rendered. Slot numbers appear <b>bold</b> in
the dropdown once a formula has been saved to them, and stay gray while
empty. Any edits you've made since the last render are kept in memory
automatically when you switch slots — nothing is lost.</p>
<p><b>Two slots may share the same output name.</b> Slots are
independent, so having <code>A = B+C</code> in slot 1 and
<code>A = D+E</code> in slot 3 is allowed.</p>
<p><b>Process is always clickable.</b> If something is missing or the
current field's text doesn't match what was last rendered for this
slot, clicking Process shows a popup explaining exactly what to fix.</p>

<h3>Process All</h3>
<p>Runs every slot that has a formula in one go and shows a single
summary table, one row per function. Each slot uses its own stored
variables, output unit, Iterations, Seed and Eval limits, so slots do
not need to be rendered first, and a row matches what Process gives for
that slot alone.</p>
<p>Before anything runs, every slot is checked. If some have missing or
invalid inputs, a popup lists each one and why, and asks whether to
<b>Continue</b> without them or <b>Cancel</b>. A broken subfunction stops
the whole run instead, since subfunctions are shared by every slot. A
function that fails while calculating (a unit mismatch, for example) is
listed under <b>Not processed</b> in the results window.</p>
<p>The table groups its columns under headers:</p>
<ul>
<li><b>Function</b> &mdash; Slot, Var, Var Name, Unit</li>
<li><b>Monte Carlo</b> &mdash; Iterations, Seed, Mean, Min, Max</li>
<li><b>Worst Case</b> &mdash; Min, Max</li>
<li><b>RSS &plusmn;3&sigma;</b> &mdash; Min, Max: the all-nominal output
   &plusmn; 3 RSS &sigma;</li>
<li><b>Eval Limits</b> &mdash; Eval Min, Eval Max, Fail %, Cp, Cpk.
   Shown only when at least one processed function has an Eval
   limit.</li>
</ul>
<p>When a row has an Eval Min and/or Eval Max, its Monte Carlo
<b>Mean</b>, <b>Min</b> and <b>Max</b>, and its <b>Worst Case</b> and
<b>RSS &plusmn;3&sigma;</b> Min/Max values are shown in <span style="color:#2e7d32">green</span> when inside
the limits and <span style="color:#c62828">red</span> when outside (a value
equal to a limit counts as inside). Rows without Eval limits stay
black.</p>
<p>The exported PDF also includes an <b>Input Variables</b> table (it is
not shown in the results window). It lists every variable the processed
functions use, with its Tol Type, limits, Nominal, &plusmn; Tol, Tol Band,
Unit, Distribution and Cp &mdash; as they stood when Process All ran, so
later edits don't change a report already shown.</p>
<p>Samples that produce an undefined result (see Invalid / NaN Results
below) are left out of that row's statistics, and a note under the table
lists which slots had any and how many. A worst case marked <b>*</b> is
the approximate independent-swing estimate.</p>
<p>Process All has no distribution charts and does not run sensitivity
analysis or the convergence check. <b>Export Report (PDF)</b> saves the
results table as a landscape PDF, with a heavier border around each
column group, followed by the Input Variables table on its own page(s); the Not processed list stays in the window and
is not printed.</p>
<p>The name of the project file last loaded or saved is shown next to
<b>Load Inputs</b> (hover over it for the full path). It changes only when
a save or load succeeds.</p>
<p><b>Save Inputs only saves variables/outputs that are currently
referenced</b> by at least one non-empty slot or subfunction.</p>

<h3>Subfunctions</h3>
<p>Click the <b>[+]</b> next to the Function field's preview to add an
intermediate quantity shared across ALL function slots — e.g.
<code>k_1 = a*T + b</code>. Each subfunction row has its own
optional Function Desc field. Chains of subfunctions are supported: a
subfunction may refer to another subfunction, and they are substituted
repeatedly until only input variables remain. The big "Rendered
Expression" display shows only the FINAL, fully solved main function for
the currently rendered slot.</p>
<p><b>Names must be unique.</b> Render is refused, with a popup naming
the conflict, if:</p>
<ul>
<li>Two subfunctions define the same name — only one could ever take
   effect, so the other would be silently ignored</li>
<li>A subfunction uses the same name as any main function's output, in
   any slot — such a subfunction would never be substituted into
   anything</li>
</ul>
<p><b>Circular references are refused.</b> Definitions that depend on
each other in a loop (<code>X = Y+1</code> together with
<code>Y = X+1</code>, or a self-reference like <code>A = sin(A)</code>)
can never be reduced to input variables. The popup shows the exact loop,
e.g. <code>X → Y → X</code>.</p>
<p><b>Show only used</b> — the checkbox beside Render/Update Table hides
subfunction rows the rendered function doesn't actually depend on,
including ones reached only through other subfunctions. This is a
display filter ONLY: hidden rows are still parsed, still checked for
name conflicts, and still written by Save Inputs. Unchecking shows
everything again, and switching slots resets it.</p>

<h3>Input Variables Table</h3>
<p><b>Show only used</b> &mdash; the checkbox at the right of the
Input Variables heading, checked by default. Checked, the table lists only
the variables the displayed function uses. Unchecked, it also lists every
other input variable used by any function slot or subfunction, after the
function's own and with the Var name in <span style="color:red">red</span>.
Red rows are fully editable and saved with the project, so you can set up
another slot's variables without switching to it, but they are not part
of the displayed function: they are not sampled, a missing value in one
doesn't block Process, and editing one doesn't gray out Results.</p>
<p><b>Tol Type</b> controls the row. While it is blank, the Upper,
Lower and Nominal fields stay disabled.</p>
<ul>
<li><b>Symmetric</b> — enter Upper and Lower only. <b>Nominal is filled
   in for you</b> as the midpoint <code>(upper + lower) / 2</code> and is
   grayed out, so you never have to type it.</li>
<li><b>Asymmetric</b> — you enter Nominal yourself, because an
   off-center nominal is the whole point of this mode.</li>
<li><b>Fixed</b> — the variable has no tolerance at all. You enter only
   Nominal; Upper and Lower stay grayed out and <b>mirror the Nominal
   value</b>, and ± Tol / Tol Band read <code>±0</code> and <code>0</code>.
   The Distribution column locks to <code>Fixed</code>, since a value with
   no spread has only one possible distribution.</li>
</ul>
<p>The <b>Distribution</b> column only ever offers choices that match the
row's Tol Type: <code>Fixed</code> is available exclusively to a Fixed
row, and Normal / Uniform / Triangular / Lognormal exclusively to rows
with real limits. A Fixed variable contributes nothing to the output
spread anywhere — no Monte Carlo scatter, no OAT swing, no RSS
contribution, and a worst-case corner that stays at Nominal.</p>
<p>Two grayed-out columns are calculated automatically and are never
typed into or saved:</p>
<ul>
<li><b>± Tol</b> — the tolerance as a deviation from Nominal. Shows a
   single value like <code>±12.5</code> when both sides match, or
   <code>+20 / -5</code> when they differ.</li>
<li><b>Tol Band</b> — the total spread between the limits,
   <code>|upper - lower|</code>. Upper 100 with Lower 75 gives
   <code>25</code>. This does not involve Nominal, so it still displays
   while Nominal is blank or mid-edit.</li>
</ul>

<h3>Cp Column</h3>
<p>The process capability of whatever supplies that variable. It sets how
the tolerance width relates to the real spread:
<code>width = 6 &times; Cp &times; &sigma;</code>.</p>
<ul>
<li><b>Cp = 1.00</b> (default) &mdash; limits sit at &plusmn;3&sigma;. The
   usual textbook assumption.</li>
<li><b>Cp = 1.33</b> &mdash; the process is tighter than its tolerance;
   true &sigma; is <code>width / 7.98</code>. Assuming 1.00 for a capable
   process <b>overstates</b> its contribution to output spread.</li>
<li><b>Cp &lt; 1.00</b> &mdash; the process cannot hold its tolerance;
   spread is wider than the limits suggest.</li>
</ul>
<p>Applies to <b>Normal</b> and <b>Lognormal</b> only, and the field is
disabled elsewhere: Uniform and Triangular fill their limits by
definition, and a Fixed variable has no spread. Cp also feeds the RSS
estimate and tolerance allocation, so those stay consistent with the
Monte Carlo draw.</p>
<p>Not to be confused with the <b>Cp / Cpk</b> reported after a run:
that pair measures the output against the Eval limits. This column is
an input, describing the process behind one variable.</p>

<h3>Units</h3>
<ul>
<li>Enter units in plain text: <code>psi</code>, <code>mm</code>, <code>kg</code>, etc.</li>
<li>For temperature use <code>degC</code> or <code>degF</code> — bare <code>C</code>
   means "coulomb." Temperatures are auto-converted to Kelvin before arithmetic runs.</li>
<li>For a dimensionless constant, type <code>dimensionless</code>.</li>
<li>The small preview text under each Unit field shows what the app resolved it to.</li>
</ul>

<h3>Iterations, Seed and Eval Min/Max</h3>
<p>These four belong to the function slot, not to the app. A different
slot solves for a different output with its own acceptance limits, and an
iteration count that has converged for one equation has not necessarily
converged for another, so each slot remembers its own and they are saved
that way in the project file.</p>
<p>They are cleared when you switch slots and reappear when you click
<b>Render / Update Table</b>, along with that slot's variables and output
unit. Until you render, Process stays grayed out and says Iterations is
missing &mdash; the same &quot;render before you process&quot; rule that
already applies to the variables table.</p>

<h3>Iterations</h3>
<p>How many random samples the Monte Carlo run draws. More samples means
less random scatter in the results, but the return diminishes: precision
improves as <code>1/&radic;n</code>, so <b>4&times; the iterations only
halves</b> the &plusmn; figures. Going from 10,000 to 20,000 buys about a
30% improvement, not 50%.</p>
<p>You do not need to guess a number. Run once, then read the
<b>VERDICT</b> line in the Convergence section of the results &mdash; it
either confirms the count is enough or names the count to use. See
<i>Convergence</i> below.</p>
<p>Cost scales linearly with iterations, and the sampling itself is fast;
on a typical model the deterministic analyses take longer than the draw.
So an iteration count that looks generous is usually cheap.</p>

<h3>Seed</h3>
<p>Leave blank for fresh randomness each run. Enter an integer to make
the Monte Carlo run reproducible.</p>
<p>The seed fixes the sampled draw, and with it every number in the
report. It does not fix the bootstrap resampling behind the confidence
whiskers on the Pearson, SRC and Variance Contribution charts, so those
error bars still shift slightly from run to run.</p>

<h3>Annotations (picture icon)</h3>
<p>Opens a paste-and-arrange workspace: press <code>Ctrl+V</code> to drop
clipboard images onto letter-sized sheets, drag to move them,
<code>Shift+Wheel</code> to resize, and <code>Ctrl+P</code> to export every
sheet as a multi-page PDF. Tabs along the bottom add, remove, and rename
sheets.</p>
<p>Sheets and their images are stored inside the same project file as
your functions and variables, so Save Inputs / Load Inputs round-trips
them together.</p>

<h3>Settings (gear icon)</h3>
<p><b>Rendered Expression Style</b> controls how the solved equation is
displayed. This ONLY changes what you see; it never changes the actual
Monte Carlo calculation, which always uses the true solved expression
regardless of display style. Options like Factored or Partial Fractions
are most useful for single-variable rational expressions — on complex
multi-variable equations they may simply have no visible effect.</p>
<p><b>Monte Carlo Distribution Chart</b> controls how the output
distribution is plotted after Process runs:</p>
<ul>
<li><b>Histogram</b> — raw sample counts per bin (the default).</li>
<li><b>Relative Frequency Histogram</b> — bars show percentage of total
   samples instead of raw counts.</li>
<li><b>Standardized Relative Histogram</b> — same as above, but the
   x-axis is converted to Z-scores (standard deviations from the mean)
   instead of the output's real units. Eval Min/Max lines are
   automatically converted to Z-scores too, so they stay meaningful on
   this axis.</li>
<li><b>CDF</b> — cumulative distribution function; each Eval Min/Max
   line is annotated with the exact percentage of samples that fall
   outside it, read directly off the curve.</li>
</ul>
<p><b>Sensitivity Analysis Method</b> picks the method Process uses to
rank the input variables (see Sensitivity Methods below). Process All
does not run it.</p>
<p><b>Report Decimal Places (max)</b> rounds every number in both
reports, on screen and in the PDF, to at most this many decimal places
(default 5). Trailing zeros are dropped, so a value with fewer decimals is
shown as it is: 0.125 stays 0.125 at 5 places while 18.491403 becomes
18.4914 at 4. Numbers are never written in scientific notation, so a very
small value rounds toward zero. Chart labels are not affected. The
setting is fixed into each run, so reopening a report with
<b>Results</b> shows it as it was run; change it and run again to see the
new rounding.</p>

<h2>Sensitivity Methods — What Each One Tells You</h2>

<h3>Pearson Correlation</h3>
<p>Correlates each input's samples against the output. Captures only
linear relationships. Error bars show a bootstrap 90% confidence interval.</p>

<h3>Standardized Regression Coefficients (SRC)</h3>
<p>Linear regression of standardized output vs. all standardized inputs
at once, controlling for co-varying inputs. R² below 0.9 signals a poor
linear fit (nonlinear/interaction effects).</p>

<h3>Variance Contribution (%)</h3>
<p>Each variable's share of the output variance, summing to 100%. This is
the squared SRC coefficient normalized, so it carries the same information
as SRC but in the form you act on: "this one variable accounts for 84% of
the spread, the other five share the rest".
Check the R&sup2; in the title &mdash; a low value means variance is going
into nonlinear or interaction effects these shares cannot see, and Sobol
is the better tool.</p>

<h3>Sobol Indices</h3>
<p>Captures nonlinear and interaction effects. Reports S1 (main effect)
and ST (total effect). Uses its own dedicated Saltelli sampling and
assumes each variable is drawn UNIFORMLY between its limits — the
Distribution column is not used for this method. Fixed variables are held
at Nominal and left out of the analysis, since a zero-width range has no
index to report.</p>
<p>If the output is undefined (NaN) for some of Sobol's own samples, the
affected sample groups are left out and a note under the chart says how
many; the indices then describe only the part of the input range where the
function is defined. If more than half the groups are affected, no indices
are reported and the popup says why &mdash; Sobol samples every variable
uniformly across its full limits, so it can reach combinations the Monte
Carlo draw rarely does.</p>

<h3>OAT Tornado (Deterministic)</h3>
<p>Sweeps one variable at a time to its limit, holding others at
Nominal, and reports the output swing in real units. Fully captures
nonlinear terms but not combined worst-case effects.</p>

<h3>Elasticity (% / %)</h3>
<p>Answers a different question from the four methods above. They all
report how much of the output's <i>uncertainty</i> each input is
responsible for. Elasticity ignores the tolerances and reports leverage:
if this input were 1% different, how far would the output move, in
percent? An input can be known to a tenth of a thou (negligible variance
share) and still be the biggest lever in the equation.</p>
<p>It is derived from the equation with calculus, not measured from the
samples, so it does not move when you change the seed or the iteration
count. Where a variable enters as a power, the elasticity <i>is</i> that
power, exactly: a variable appearing as <code>x^2</code> reads
<code>+2</code> (1% up on <code>x</code> puts roughly 2% on the output),
one appearing as <code>1/x^4</code> reads <code>&minus;4</code>, and one
that enters linearly and alone reads <code>+1</code>.</p>
<p>Each bar is labelled with how far it can be trusted:</p>
<ul>
<li><b>constant exponent</b> &mdash; a true power law in that variable.
   The number is exact everywhere, not just at Nominal.</li>
<li><b>local only</b> &mdash; the exponent changes across the input's
   range. The bar is the value at Nominal and the whisker shows how far it
   travels between that input's limits.</li>
<li><b>linear stack-up</b> &mdash; for an additive equation the
   elasticity is just each term's share of the nominal total, which
   Variance Contribution already reports more usefully.</li>
<li><b>no effect</b> &mdash; the output does not move with this input at
   all.</li>
<li><b>not defined</b> &mdash; a percentage change needs something to be a
   percentage of. An output that sits at or near zero (a clearance, a
   margin, a difference of two similar numbers) has no meaningful percent
   change, so those variables get a note instead of a bar. Use the OAT
   tornado for those.</li>
</ul>
<p>Whiskers here are a swept range, <b>not</b> a confidence interval as
they are on the sampled methods. Every elasticity is measured with the
other variables held at Nominal, so the whisker is not the spread across
the full operating envelope.</p>

<h2>Other Numbers Always Shown After Processing</h2>

<h3>Reported Function</h3>
<p>The results panel and the exported PDF show the <b>fully solved</b>
expression — every subfunction substituted in — which is exactly what
the simulation evaluated, not the shorter text you typed. It follows the
Rendered Expression Style set in Settings.</p>
<p>Above it, <b>Output</b> shows the output's Var Name and unit (a dash
if the Var Name is blank), and <b>Description</b> shows the slot's
Function Desc.</p>

<h3>Cp / Cpk</h3>
<p>Process-capability indices. <b>Cp</b> needs both Eval Min and Eval Max,
since it measures the spec width against the spread. <b>Cpk</b> appears
with just one of them, using whichever limits are present. Both require a
non-zero standard deviation.</p>

<h3>OAT Baseline</h3>
<p>The output value with every variable at its Nominal — the reference
point the OAT Tornado swings are measured from.</p>

<h3>RSS Estimate</h3>
<p>Classic linear tolerance-stack approximation (limits treated as
&plusmn;3&sigma;, combined in quadrature). Sanity-check against the Monte
Carlo standard deviation.</p>
<p>The <b>RSS range at &plusmn;3&sigma;</b> line converts that &sigma; into
real output limits, <code>OAT Baseline &plusmn; 3&sigma;</code>. This is the
statistical stack-up range, and it should always sit inside the
worst-case stack-up — that gap is exactly what you gain by not assuming
every variable goes bad at once.</p>

<h3>Convergence</h3>
<p>Monte Carlo results are themselves estimates &mdash; run again with a
different seed and they move. This block shows how much they move, then
tells you outright whether your iteration count is enough.</p>
<p>The <b>&plusmn;</b> after each figure is how far it would shift on a
re-run with a different seed. Practically: digits beyond that are noise.
If the mean reads <code>14.44 &plusmn; 0.02</code>, quote 14.44 &mdash;
not 14.4382.</p>
<p>The <b>VERDICT</b> line is the answer. It either confirms the count is
enough (and says how far you could cut it, if the saving is worthwhile) or
names the count to increase to. You do not need to interpret the
&plusmn; figures yourself.</p>
<p>See <i>Iterations</i> above for why raising the count has diminishing
returns.</p>
<p>The failure rate is usually what decides it. An average settles on a
few thousand samples, but a rare event needs roughly <b>100 occurrences</b>
before its rate is stable &mdash; so a 0.3% failure rate needs around
30,000&ndash;50,000 iterations while the mean was fine at 2,500. When that
is the limiting factor the block says so explicitly.</p>
<p>If no failures occur at all, no rate can be measured; the block reports
the upper bound the run does establish instead.</p>
<p>What it cannot tell you: whether the <i>model</i> is right. These
figures describe sampling precision only. A mistyped tolerance or a wrong
Cp will still report a beautifully tight &plusmn; on the wrong answer.</p>

<h3>Tolerance Allocation</h3>
<p>The inverse of everything else here. Instead of "given these
tolerances, what output do I get?", it answers "I need the output within
this spec at this Cpk &mdash; what tolerance can each variable have?"
Enabled after a Process run, since it works from that run's sensitivities.</p>
<ul>
<li><b>Uniform</b> &mdash; scales every tolerance by a single factor,
   preserving the scheme you chose.</li>
<li><b>Equal-Contribution</b> &mdash; gives every variable the same share
   of the variance budget, tightening sensitive variables and loosening
   insensitive ones. Usually the cheaper scheme to buy.</li>
</ul>
<p>The allowable &sigma; comes from the <i>nearest</i> spec limit, not the
full width, so an off-centre design is not quietly permitted to miss the
near limit. If the all-nominal output falls outside the spec entirely, no
tolerance can fix that and the dialog says so &mdash; adjust nominals
first.</p>
<p>Both schemes assume each variable's contribution is proportional to its
tolerance, the same linear model behind the RSS estimate. Re-run Process
with the new tolerances to confirm, especially for a nonlinear
equation.</p>

<h3>Worst-Case Stack-Up</h3>
<p>True output range with every variable at its worst limit
simultaneously. The results panel always states which method produced
it.</p>
<ul>
<li><b>Linear functions</b> — a plain sum or difference of variables, with
   no products, powers, roots or divisions between them. Each variable's
   contribution is independent of the others, so the worst corner is found
   directly from the one-at-a-time swings. This is <b>exact</b>, not an
   estimate, and it is instant no matter how many variables you have.
   Typical tolerance stack-ups take this path.</li>
<li><b>Everything else</b> — all <code>2^n</code> limit corners are
   enumerated, which is also <b>exact</b>. Cost doubles with each added
   variable, so above 20 varying variables an approximate
   independent-swing estimate is used instead. Fixed variables don't count
   toward that total, since they contribute only one value.</li>
</ul>
<p>A Fixed variable has no limits, so it sits at Nominal in every corner
and never widens the range.</p>

<h3>Invalid / NaN Results</h3>
<p>If your equation includes a square root or logarithm, some or all
Monte Carlo samples can produce an undefined result (e.g. the square
root of a negative number) if two variables' tolerance ranges are
physically inconsistent with each other. If EVERY sample is invalid,
Process stops with an explanation. If only SOME samples are invalid,
you'll be asked whether to continue using just the valid samples.
Process All doesn't ask: it leaves them out and a note under its table
says how many.</p>
<p>When you continue, each excluded result is removed together with the
input values that produced it, so the sensitivity methods still compare
every input with its own output. The report's Monte Carlo Results section
then shows an <b>Invalid</b> line with how many samples were excluded
(it reads <i>Invalid: 0</i> when none were).</p>
"""
