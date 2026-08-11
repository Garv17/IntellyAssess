"""Java codec/driver for the function-signature judge.

Java ships no JSON library in the JDK, and the sandbox's eclipse-temurin image has
no network access to fetch one at judge time — so CODEC below includes a small
hand-rolled JSON reader restricted to the shapes this system actually needs
(numbers, booleans, strings, arrays, and one level of object nesting for the
`{"params": [...]}` envelope). It is not a general-purpose JSON library.

The sandbox's Java run_cmd is fixed to `java Main` against a file named
`Main.java` (see app.services.sandbox.LANGUAGES["java"]), so exactly one class in
the assembled source may be `public`: the driver's Main below. Codec and the
student's Solution class are deliberately package-private.
"""

from __future__ import annotations

_TYPES = {
    "int": "int",
    "float": "double",
    "bool": "boolean",
    "string": "String",
    "char": "char",
    "int[]": "int[]",
    "float[]": "double[]",
    "bool[]": "boolean[]",
    "string[]": "String[]",
    "int[][]": "int[][]",
}
_DECODE = {
    "int": "asInt",
    "float": "asDouble",
    "bool": "asBool",
    "string": "asString",
    "char": "asChar",
    "int[]": "asIntArray",
    "float[]": "asDoubleArray",
    "bool[]": "asBoolArray",
    "string[]": "asStringArray",
    "int[][]": "asIntMatrix",
}
_ENCODE = {
    "int": "encodeInt",
    "float": "encodeDouble",
    "bool": "encodeBool",
    "string": "encodeString",
    "char": "encodeChar",
    "int[]": "encodeIntArray",
    "float[]": "encodeDoubleArray",
    "bool[]": "encodeBoolArray",
    "string[]": "encodeStringArray",
    "int[][]": "encodeIntMatrix",
}

CODEC = r"""
final class Codec {
    static java.util.List<Object> parseParams(String s) {
        int[] i = {0};
        Object root = parseValue(s, i);
        java.util.Map<?, ?> map = (java.util.Map<?, ?>) root;
        @SuppressWarnings("unchecked")
        java.util.List<Object> params = (java.util.List<Object>) map.get("params");
        return params;
    }

    private static void skipWs(String s, int[] i) {
        while (i[0] < s.length() && Character.isWhitespace(s.charAt(i[0]))) i[0]++;
    }

    private static Object parseValue(String s, int[] i) {
        skipWs(s, i);
        char c = s.charAt(i[0]);
        if (c == '{') return parseObject(s, i);
        if (c == '[') return parseArray(s, i);
        if (c == '"') return parseString(s, i);
        if (c == 't' || c == 'f') return parseBool(s, i);
        if (c == 'n') { i[0] += 4; return null; }
        return parseNumber(s, i);
    }

    private static java.util.Map<String, Object> parseObject(String s, int[] i) {
        java.util.Map<String, Object> map = new java.util.HashMap<>();
        i[0]++;
        skipWs(s, i);
        if (s.charAt(i[0]) == '}') { i[0]++; return map; }
        while (true) {
            skipWs(s, i);
            String key = parseString(s, i);
            skipWs(s, i);
            i[0]++;
            Object val = parseValue(s, i);
            map.put(key, val);
            skipWs(s, i);
            char c = s.charAt(i[0]);
            if (c == ',') { i[0]++; continue; }
            i[0]++;
            break;
        }
        return map;
    }

    private static java.util.List<Object> parseArray(String s, int[] i) {
        java.util.List<Object> list = new java.util.ArrayList<>();
        i[0]++;
        skipWs(s, i);
        if (s.charAt(i[0]) == ']') { i[0]++; return list; }
        while (true) {
            list.add(parseValue(s, i));
            skipWs(s, i);
            char c = s.charAt(i[0]);
            if (c == ',') { i[0]++; continue; }
            i[0]++;
            break;
        }
        return list;
    }

    private static String parseString(String s, int[] i) {
        i[0]++;
        StringBuilder sb = new StringBuilder();
        while (s.charAt(i[0]) != '"') {
            char c = s.charAt(i[0]);
            if (c == '\\') {
                i[0]++;
                char esc = s.charAt(i[0]);
                switch (esc) {
                    case 'n': sb.append('\n'); break;
                    case 't': sb.append('\t'); break;
                    case 'r': sb.append('\r'); break;
                    case '"': sb.append('"'); break;
                    case '\\': sb.append('\\'); break;
                    case '/': sb.append('/'); break;
                    default: sb.append(esc);
                }
            } else {
                sb.append(c);
            }
            i[0]++;
        }
        i[0]++;
        return sb.toString();
    }

    private static Boolean parseBool(String s, int[] i) {
        if (s.charAt(i[0]) == 't') { i[0] += 4; return Boolean.TRUE; }
        i[0] += 5;
        return Boolean.FALSE;
    }

    private static Double parseNumber(String s, int[] i) {
        int start = i[0];
        while (i[0] < s.length() && "-+.eE0123456789".indexOf(s.charAt(i[0])) >= 0) i[0]++;
        return Double.parseDouble(s.substring(start, i[0]));
    }

    static int asInt(Object o) { return ((Double) o).intValue(); }
    static double asDouble(Object o) { return ((Double) o).doubleValue(); }
    static boolean asBool(Object o) { return (Boolean) o; }
    static String asString(Object o) { return (String) o; }
    static char asChar(Object o) { return ((String) o).charAt(0); }

    static int[] asIntArray(Object o) {
        java.util.List<?> list = (java.util.List<?>) o;
        int[] out = new int[list.size()];
        for (int k = 0; k < list.size(); k++) out[k] = asInt(list.get(k));
        return out;
    }
    static double[] asDoubleArray(Object o) {
        java.util.List<?> list = (java.util.List<?>) o;
        double[] out = new double[list.size()];
        for (int k = 0; k < list.size(); k++) out[k] = asDouble(list.get(k));
        return out;
    }
    static boolean[] asBoolArray(Object o) {
        java.util.List<?> list = (java.util.List<?>) o;
        boolean[] out = new boolean[list.size()];
        for (int k = 0; k < list.size(); k++) out[k] = asBool(list.get(k));
        return out;
    }
    static String[] asStringArray(Object o) {
        java.util.List<?> list = (java.util.List<?>) o;
        String[] out = new String[list.size()];
        for (int k = 0; k < list.size(); k++) out[k] = asString(list.get(k));
        return out;
    }
    static int[][] asIntMatrix(Object o) {
        java.util.List<?> rows = (java.util.List<?>) o;
        int[][] out = new int[rows.size()][];
        for (int k = 0; k < rows.size(); k++) out[k] = asIntArray(rows.get(k));
        return out;
    }

    static String encodeInt(int v) { return Integer.toString(v); }
    static String encodeDouble(double v) { return Double.toString(v); }
    static String encodeBool(boolean v) { return Boolean.toString(v); }
    static String encodeString(String v) { return "\"" + escape(v) + "\""; }
    static String encodeChar(char v) { return "\"" + escape(String.valueOf(v)) + "\""; }

    static String encodeIntArray(int[] v) {
        StringBuilder sb = new StringBuilder("[");
        for (int k = 0; k < v.length; k++) { if (k > 0) sb.append(","); sb.append(v[k]); }
        return sb.append("]").toString();
    }
    static String encodeDoubleArray(double[] v) {
        StringBuilder sb = new StringBuilder("[");
        for (int k = 0; k < v.length; k++) { if (k > 0) sb.append(","); sb.append(v[k]); }
        return sb.append("]").toString();
    }
    static String encodeBoolArray(boolean[] v) {
        StringBuilder sb = new StringBuilder("[");
        for (int k = 0; k < v.length; k++) { if (k > 0) sb.append(","); sb.append(v[k]); }
        return sb.append("]").toString();
    }
    static String encodeStringArray(String[] v) {
        StringBuilder sb = new StringBuilder("[");
        for (int k = 0; k < v.length; k++) { if (k > 0) sb.append(","); sb.append(encodeString(v[k])); }
        return sb.append("]").toString();
    }
    static String encodeIntMatrix(int[][] v) {
        StringBuilder sb = new StringBuilder("[");
        for (int k = 0; k < v.length; k++) { if (k > 0) sb.append(","); sb.append(encodeIntArray(v[k])); }
        return sb.append("]").toString();
    }

    private static String escape(String s) {
        return s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n");
    }
}
""".strip()


def boilerplate(function_name: str, return_type: str, parameters: list[dict]) -> str:
    params = ", ".join(f"{_TYPES[p['type']]} {p['name']}" for p in parameters)
    ret = _TYPES[return_type]
    return (
        "class Solution {\n"
        f"    public {ret} {function_name}({params}) {{\n"
        "        \n"
        "    }\n"
        "}\n"
    )


def driver(function_name: str, return_type: str, parameters: list[dict]) -> str:
    lines = [
        "public class Main {",
        "    public static void main(String[] args) throws Exception {",
        "        String __input = new String(System.in.readAllBytes());",
        "        java.util.List<Object> __params = Codec.parseParams(__input);",
    ]
    call_args = []
    for idx, p in enumerate(parameters):
        var = f"__p{idx}"
        lines.append(
            f"        {_TYPES[p['type']]} {var} = Codec.{_DECODE[p['type']]}(__params.get({idx}));"
        )
        call_args.append(var)
    lines.append(
        f"        {_TYPES[return_type]} __result = new Solution().{function_name}({', '.join(call_args)});"
    )
    lines.append(f"        System.out.println(Codec.{_ENCODE[return_type]}(__result));")
    lines.append("    }")
    lines.append("}")
    return "\n".join(lines)
