/**
 * Bundles Monaco locally instead of letting @monaco-editor/react fetch it from a CDN.
 *
 * Exam halls routinely sit behind restrictive networks or a proxy allowlist. A CDN
 * fetch that fails means the code editor never renders — which reads to the student
 * as "the exam is broken". Bundling serves it from the same origin as everything else.
 *
 * Only the generic editor worker is included. Monaco's TS/CSS/HTML language services
 * are ~8 MB of assets that do nothing for the languages the judge actually runs.
 */
import { loader } from '@monaco-editor/react';
import editorWorker from 'monaco-editor/esm/vs/editor/editor.worker?worker';
import * as monaco from 'monaco-editor/esm/vs/editor/editor.api';

// Syntax highlighting for the languages we allow. These are the lightweight
// tokenizer contributions, not the heavy language services.
import 'monaco-editor/esm/vs/basic-languages/python/python.contribution';
// Also registers the 'c' language id — Monaco tokenizes C under the cpp contribution.
import 'monaco-editor/esm/vs/basic-languages/cpp/cpp.contribution';
import 'monaco-editor/esm/vs/basic-languages/java/java.contribution';
import 'monaco-editor/esm/vs/basic-languages/javascript/javascript.contribution';
import 'monaco-editor/esm/vs/basic-languages/csharp/csharp.contribution';
import 'monaco-editor/esm/vs/basic-languages/php/php.contribution';
import 'monaco-editor/esm/vs/basic-languages/sql/sql.contribution';

self.MonacoEnvironment = {
  getWorker() {
    return new editorWorker();
  },
};

loader.config({ monaco });

export default monaco;
