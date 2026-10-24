Space Analyzer interface catalogue

it.json contains canonical Italian message keys. translations.tsv has one
row per translated key: numeric index, English, German, French and Spanish,
separated by vertical bars. Technical identifiers/formula functions need
no translation. Each configured message has all four target translations.

Regenerate the shipped browser catalogue with:
  node tools/language_release/catalog.cjs
from the extracted release folder. The generated file is
source/static/i18n-catalog.js. No translation service is used at runtime.

New UI text should use I18N.t(originalItalianText). Bind labels that must
update while open using I18N.bind(element,'textContent',renderer,true).
For text mixed with operator data use I18N.join with {text:...} for fixed
UI fragments and {value:...} for untouched names, paths and numeric data.
Bind raw signal names and SSH output with translation disabled.

The initial static HTML uses data-i18n and data-i18n-title/placeholder/
aria-label. Existing charts cloned from the HTML retain these markers.
Language changes update current nodes; they do not rebuild forms or
replace chart controls/listeners. Source code is already instrumented:
do not rerun the one-off instrument.cjs/extra_localize.cjs scripts on it.

For verification see tests/test_i18n.py and tests/test_i18n_ui.cjs.
