"""C codec/driver for the function-signature judge.

C is the outlier of the five: no classes, no dynamic arrays with built-in length
tracking, no JSON library. This follows the classic LeetCode-C idiom —
* array parameters take an explicit companion `int nameSize`
* matrix parameters take a companion `int nameSize` (row count) and
  `int* nameColSize` (per-row column counts)
* an array/matrix RETURN type means the student's function gains extra
  out-parameters (`int* returnSize`, and for a matrix also
  `int** returnColumnSizes`) that it must fill in — exactly like real
  LeetCode-C signatures (e.g. `int* twoSum(int* nums, int numsSize, int target,
  int* returnSize)`).

Like Java/C++, CODEC includes a small hand-rolled JSON reader restricted to the
shapes this system actually needs — not a general-purpose JSON library, since
gcc:13 ships none and the sandbox has no network access to fetch one.
"""

from __future__ import annotations

_TYPES = {
    "int": "int",
    "float": "double",
    "bool": "bool",
    "string": "char*",
    "char": "char",
    "int[]": "int*",
    "float[]": "double*",
    "bool[]": "bool*",
    "string[]": "char**",
    "int[][]": "int**",
}
_ARRAY_TYPES = {"int[]", "float[]", "bool[]", "string[]"}
_MATRIX_TYPES = {"int[][]"}
_SCALAR_DECODE = {
    "int": "as_int",
    "float": "as_double",
    "bool": "as_bool",
    "string": "as_string",
    "char": "as_char",
}
_ARRAY_DECODE = {
    "int[]": "as_int_array",
    "float[]": "as_double_array",
    "bool[]": "as_bool_array",
    "string[]": "as_string_array",
}
_SCALAR_ENCODE = {
    "int": "json_encode_int",
    "float": "json_encode_double",
    "bool": "json_encode_bool",
    "string": "json_encode_string",
    "char": "json_encode_char",
}
_ARRAY_ENCODE = {
    "int[]": "json_encode_int_array",
    "float[]": "json_encode_double_array",
    "bool[]": "json_encode_bool_array",
    "string[]": "json_encode_string_array",
}

CODEC = r"""
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include <stdbool.h>

typedef struct JsonValue {
    int tag; /* 0=null, 1=bool, 2=number, 3=string, 4=array, 5=object */
    int b;
    double num;
    char* str;
    struct JsonValue* arr;
    int arr_len;
    char** obj_keys;
    struct JsonValue* obj_vals;
    int obj_len;
} JsonValue;

typedef struct { const char* s; int i; } JsonParser;

static void json_skip_ws(JsonParser* p) {
    while (isspace((unsigned char)p->s[p->i])) p->i++;
}

static JsonValue json_parse_value(JsonParser* p);

static JsonValue json_parse_string_raw(JsonParser* p) {
    JsonValue v; v.tag = 3;
    p->i++;
    int cap = 64, len = 0;
    char* buf = malloc(cap);
    while (p->s[p->i] != '"') {
        char c = p->s[p->i];
        char out;
        if (c == '\\') {
            p->i++;
            char e = p->s[p->i];
            switch (e) {
                case 'n': out = '\n'; break;
                case 't': out = '\t'; break;
                case 'r': out = '\r'; break;
                case '"': out = '"'; break;
                case '\\': out = '\\'; break;
                case '/': out = '/'; break;
                default: out = e;
            }
        } else {
            out = c;
        }
        if (len + 1 >= cap) { cap *= 2; buf = realloc(buf, cap); }
        buf[len++] = out;
        p->i++;
    }
    p->i++;
    buf[len] = '\0';
    v.str = buf;
    return v;
}

static JsonValue json_parse_number(JsonParser* p) {
    JsonValue v; v.tag = 2;
    int start = p->i;
    while (p->s[p->i] && (isdigit((unsigned char)p->s[p->i]) || p->s[p->i] == '-' || p->s[p->i] == '+' || p->s[p->i] == '.' || p->s[p->i] == 'e' || p->s[p->i] == 'E')) p->i++;
    int len = p->i - start;
    char buf[64];
    memcpy(buf, p->s + start, len);
    buf[len] = '\0';
    v.num = atof(buf);
    return v;
}

static JsonValue json_parse_bool(JsonParser* p) {
    JsonValue v; v.tag = 1;
    if (p->s[p->i] == 't') { v.b = 1; p->i += 4; } else { v.b = 0; p->i += 5; }
    return v;
}

static JsonValue json_parse_array(JsonParser* p) {
    JsonValue v; v.tag = 4;
    int cap = 8;
    v.arr = malloc(sizeof(JsonValue) * cap);
    v.arr_len = 0;
    p->i++; json_skip_ws(p);
    if (p->s[p->i] == ']') { p->i++; return v; }
    while (1) {
        if (v.arr_len == cap) { cap *= 2; v.arr = realloc(v.arr, sizeof(JsonValue) * cap); }
        v.arr[v.arr_len++] = json_parse_value(p);
        json_skip_ws(p);
        if (p->s[p->i] == ',') { p->i++; json_skip_ws(p); continue; }
        p->i++;
        break;
    }
    return v;
}

static JsonValue json_parse_object(JsonParser* p) {
    JsonValue v; v.tag = 5;
    v.obj_keys = malloc(sizeof(char*) * 16);
    v.obj_vals = malloc(sizeof(JsonValue) * 16);
    v.obj_len = 0;
    p->i++; json_skip_ws(p);
    if (p->s[p->i] == '}') { p->i++; return v; }
    while (1) {
        json_skip_ws(p);
        JsonValue key = json_parse_string_raw(p);
        json_skip_ws(p);
        p->i++;
        JsonValue val = json_parse_value(p);
        v.obj_keys[v.obj_len] = key.str;
        v.obj_vals[v.obj_len] = val;
        v.obj_len++;
        json_skip_ws(p);
        if (p->s[p->i] == ',') { p->i++; continue; }
        p->i++;
        break;
    }
    return v;
}

static JsonValue json_parse_value(JsonParser* p) {
    json_skip_ws(p);
    char c = p->s[p->i];
    if (c == '{') return json_parse_object(p);
    if (c == '[') return json_parse_array(p);
    if (c == '"') return json_parse_string_raw(p);
    if (c == 't' || c == 'f') return json_parse_bool(p);
    if (c == 'n') { p->i += 4; JsonValue v; v.tag = 0; return v; }
    return json_parse_number(p);
}

static JsonValue json_get(JsonValue* obj, const char* key) {
    for (int i = 0; i < obj->obj_len; i++) {
        if (strcmp(obj->obj_keys[i], key) == 0) return obj->obj_vals[i];
    }
    JsonValue null_; null_.tag = 0;
    return null_;
}

static int as_int(JsonValue v) { return (int)v.num; }
static double as_double(JsonValue v) { return v.num; }
static bool as_bool(JsonValue v) { return v.b != 0; }
static char* as_string(JsonValue v) { return v.str; }
static char as_char(JsonValue v) { return v.str[0]; }

static int* as_int_array(JsonValue v, int* outLen) {
    int* out = malloc(sizeof(int) * (v.arr_len > 0 ? v.arr_len : 1));
    for (int i = 0; i < v.arr_len; i++) out[i] = as_int(v.arr[i]);
    *outLen = v.arr_len;
    return out;
}
static double* as_double_array(JsonValue v, int* outLen) {
    double* out = malloc(sizeof(double) * (v.arr_len > 0 ? v.arr_len : 1));
    for (int i = 0; i < v.arr_len; i++) out[i] = as_double(v.arr[i]);
    *outLen = v.arr_len;
    return out;
}
static bool* as_bool_array(JsonValue v, int* outLen) {
    bool* out = malloc(sizeof(bool) * (v.arr_len > 0 ? v.arr_len : 1));
    for (int i = 0; i < v.arr_len; i++) out[i] = as_bool(v.arr[i]);
    *outLen = v.arr_len;
    return out;
}
static char** as_string_array(JsonValue v, int* outLen) {
    char** out = malloc(sizeof(char*) * (v.arr_len > 0 ? v.arr_len : 1));
    for (int i = 0; i < v.arr_len; i++) out[i] = as_string(v.arr[i]);
    *outLen = v.arr_len;
    return out;
}
static int** as_int_matrix(JsonValue v, int* outRows, int** outColSizes) {
    int** out = malloc(sizeof(int*) * (v.arr_len > 0 ? v.arr_len : 1));
    int* colSizes = malloc(sizeof(int) * (v.arr_len > 0 ? v.arr_len : 1));
    for (int i = 0; i < v.arr_len; i++) {
        int len;
        out[i] = as_int_array(v.arr[i], &len);
        colSizes[i] = len;
    }
    *outRows = v.arr_len;
    *outColSizes = colSizes;
    return out;
}

static char* json_escape(const char* s) {
    int len = strlen(s);
    char* out = malloc(len * 2 + 3);
    int j = 0;
    out[j++] = '"';
    for (int i = 0; i < len; i++) {
        char c = s[i];
        if (c == '\\' || c == '"') { out[j++] = '\\'; out[j++] = c; }
        else if (c == '\n') { out[j++] = '\\'; out[j++] = 'n'; }
        else out[j++] = c;
    }
    out[j++] = '"';
    out[j] = '\0';
    return out;
}

static char* json_encode_int(int v) {
    char* buf = malloc(32);
    snprintf(buf, 32, "%d", v);
    return buf;
}
static char* json_encode_double(double v) {
    char* buf = malloc(64);
    snprintf(buf, 64, "%.15g", v);
    return buf;
}
static char* json_encode_bool(bool v) {
    char* buf = malloc(8);
    strcpy(buf, v ? "true" : "false");
    return buf;
}
static char* json_encode_string(const char* v) { return json_escape(v); }
static char* json_encode_char(char v) {
    char tmp[2] = { v, '\0' };
    return json_escape(tmp);
}
static char* json_encode_int_array(int* v, int n) {
    char* buf = malloc((size_t)n * 16 + 8);
    int j = 0;
    buf[j++] = '[';
    for (int i = 0; i < n; i++) {
        if (i > 0) buf[j++] = ',';
        j += snprintf(buf + j, 16, "%d", v[i]);
    }
    buf[j++] = ']';
    buf[j] = '\0';
    return buf;
}
static char* json_encode_double_array(double* v, int n) {
    char* buf = malloc((size_t)n * 32 + 8);
    int j = 0;
    buf[j++] = '[';
    for (int i = 0; i < n; i++) {
        if (i > 0) buf[j++] = ',';
        j += snprintf(buf + j, 32, "%.15g", v[i]);
    }
    buf[j++] = ']';
    buf[j] = '\0';
    return buf;
}
static char* json_encode_bool_array(bool* v, int n) {
    char* buf = malloc((size_t)n * 8 + 8);
    int j = 0;
    buf[j++] = '[';
    for (int i = 0; i < n; i++) {
        if (i > 0) buf[j++] = ',';
        const char* s = v[i] ? "true" : "false";
        int l = strlen(s);
        memcpy(buf + j, s, l);
        j += l;
    }
    buf[j++] = ']';
    buf[j] = '\0';
    return buf;
}
static char* json_encode_string_array(char** v, int n) {
    char* buf = malloc((size_t)n * 1024 + 8);
    int j = 0;
    buf[j++] = '[';
    for (int i = 0; i < n; i++) {
        if (i > 0) buf[j++] = ',';
        char* esc = json_encode_string(v[i]);
        int l = strlen(esc);
        memcpy(buf + j, esc, l);
        j += l;
        free(esc);
    }
    buf[j++] = ']';
    buf[j] = '\0';
    return buf;
}
static char* json_encode_int_matrix(int** v, int rows, int* colSizes) {
    char* buf = malloc((size_t)rows * 512 + 8);
    int j = 0;
    buf[j++] = '[';
    for (int i = 0; i < rows; i++) {
        if (i > 0) buf[j++] = ',';
        char* row = json_encode_int_array(v[i], colSizes[i]);
        int l = strlen(row);
        memcpy(buf + j, row, l);
        j += l;
        free(row);
    }
    buf[j++] = ']';
    buf[j] = '\0';
    return buf;
}
""".strip()


def _param_decl(p: dict) -> str:
    t = p["type"]
    name = p["name"]
    if t in _ARRAY_TYPES:
        return f"{_TYPES[t]} {name}, int {name}Size"
    if t in _MATRIX_TYPES:
        return f"{_TYPES[t]} {name}, int {name}Size, int* {name}ColSize"
    return f"{_TYPES[t]} {name}"


def _return_decl(return_type: str) -> tuple[str, list[str]]:
    if return_type in _ARRAY_TYPES:
        return _TYPES[return_type], ["int* returnSize"]
    if return_type in _MATRIX_TYPES:
        return _TYPES[return_type], ["int* returnSize", "int** returnColumnSizes"]
    return _TYPES[return_type], []


def boilerplate(function_name: str, return_type: str, parameters: list[dict]) -> str:
    params = [_param_decl(p) for p in parameters]
    ret_type, extra = _return_decl(return_type)
    params.extend(extra)
    return f"{ret_type} {function_name}({', '.join(params)}) {{\n    \n}}\n"


def driver(function_name: str, return_type: str, parameters: list[dict]) -> str:
    lines = [
        "int main(void) {",
        "    char* __buf = malloc(1 << 20);",
        "    size_t __n = fread(__buf, 1, (1 << 20) - 1, stdin);",
        "    __buf[__n] = '\\0';",
        "    JsonParser __jp = { __buf, 0 };",
        "    JsonValue __root = json_parse_value(&__jp);",
        '    JsonValue __params = json_get(&__root, "params");',
    ]
    call_args: list[str] = []
    for idx, p in enumerate(parameters):
        t = p["type"]
        var = f"__p{idx}"
        if t in _ARRAY_TYPES:
            lines.append(f"    int {var}Size;")
            lines.append(f"    {_TYPES[t]} {var} = {_ARRAY_DECODE[t]}(__params.arr[{idx}], &{var}Size);")
            call_args.append(var)
            call_args.append(f"{var}Size")
        elif t in _MATRIX_TYPES:
            lines.append(f"    int {var}Rows;")
            lines.append(f"    int* {var}ColSizes;")
            lines.append(f"    {_TYPES[t]} {var} = as_int_matrix(__params.arr[{idx}], &{var}Rows, &{var}ColSizes);")
            call_args.append(var)
            call_args.append(f"{var}Rows")
            call_args.append(f"{var}ColSizes")
        else:
            lines.append(f"    {_TYPES[t]} {var} = {_SCALAR_DECODE[t]}(__params.arr[{idx}]);")
            call_args.append(var)

    ret_type, _ = _return_decl(return_type)
    if return_type in _ARRAY_TYPES:
        lines.append("    int __returnSize;")
        call_args.append("&__returnSize")
    elif return_type in _MATRIX_TYPES:
        lines.append("    int __returnSize;")
        lines.append("    int* __returnColumnSizes;")
        call_args.append("&__returnSize")
        call_args.append("&__returnColumnSizes")

    lines.append(f"    {ret_type} __result = {function_name}({', '.join(call_args)});")

    if return_type in _ARRAY_TYPES:
        lines.append(f"    printf(\"%s\\n\", {_ARRAY_ENCODE[return_type]}(__result, __returnSize));")
    elif return_type in _MATRIX_TYPES:
        lines.append("    printf(\"%s\\n\", json_encode_int_matrix(__result, __returnSize, __returnColumnSizes));")
    else:
        lines.append(f"    printf(\"%s\\n\", {_SCALAR_ENCODE[return_type]}(__result));")

    lines.append("    return 0;")
    lines.append("}")
    return "\n".join(lines)
