"""C++ codec/driver for the function-signature judge.

Like Java, the gcc:13 sandbox image ships no JSON library and there's no network
access at judge time to fetch one, so CODEC below includes a small hand-rolled
JSON reader restricted to the shapes this system actually needs — not a
general-purpose JSON library.
"""

from __future__ import annotations

_TYPES = {
    "int": "int",
    "float": "double",
    "bool": "bool",
    "string": "string",
    "char": "char",
    "int[]": "vector<int>",
    "float[]": "vector<double>",
    "bool[]": "vector<bool>",
    "string[]": "vector<string>",
    "int[][]": "vector<vector<int>>",
}
# Array/matrix/string params take a reference in the generated signature, matching
# the idiomatic LeetCode-style C++ convention; scalars are passed by value.
_BY_REF = {"string", "int[]", "float[]", "bool[]", "string[]", "int[][]"}
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
#include <string>
#include <vector>
#include <utility>
#include <cctype>
#include <iostream>
#include <sstream>
#include <iterator>
#include <iomanip>
using namespace std;

struct JsonValue {
    // tag: 0=null, 1=bool, 2=number, 3=string, 4=array, 5=object
    int tag = 0;
    bool b = false;
    double num = 0;
    string str;
    vector<JsonValue> arr;
    vector<pair<string, JsonValue>> obj;
};

class JsonParser {
public:
    explicit JsonParser(const string& src) : s(src), i(0) {}
    JsonValue parse() { skipWs(); return parseValue(); }

private:
    const string& s;
    size_t i;

    void skipWs() { while (i < s.size() && isspace((unsigned char)s[i])) i++; }

    JsonValue parseValue() {
        skipWs();
        char c = s[i];
        if (c == '{') return parseObject();
        if (c == '[') return parseArray();
        if (c == '"') return parseString();
        if (c == 't' || c == 'f') return parseBool();
        if (c == 'n') { i += 4; JsonValue v; v.tag = 0; return v; }
        return parseNumber();
    }

    JsonValue parseObject() {
        JsonValue v; v.tag = 5;
        i++; skipWs();
        if (s[i] == '}') { i++; return v; }
        while (true) {
            skipWs();
            JsonValue key = parseString();
            skipWs(); i++;
            JsonValue val = parseValue();
            v.obj.push_back({key.str, val});
            skipWs();
            if (s[i] == ',') { i++; continue; }
            i++;
            break;
        }
        return v;
    }

    JsonValue parseArray() {
        JsonValue v; v.tag = 4;
        i++; skipWs();
        if (s[i] == ']') { i++; return v; }
        while (true) {
            v.arr.push_back(parseValue());
            skipWs();
            if (s[i] == ',') { i++; continue; }
            i++;
            break;
        }
        return v;
    }

    JsonValue parseString() {
        JsonValue v; v.tag = 3;
        i++;
        string out;
        while (s[i] != '"') {
            char c = s[i];
            if (c == '\\') {
                i++;
                char e = s[i];
                switch (e) {
                    case 'n': out += '\n'; break;
                    case 't': out += '\t'; break;
                    case 'r': out += '\r'; break;
                    case '"': out += '"'; break;
                    case '\\': out += '\\'; break;
                    case '/': out += '/'; break;
                    default: out += e;
                }
            } else {
                out += c;
            }
            i++;
        }
        i++;
        v.str = out;
        return v;
    }

    JsonValue parseBool() {
        JsonValue v; v.tag = 1;
        if (s[i] == 't') { v.b = true; i += 4; } else { v.b = false; i += 5; }
        return v;
    }

    JsonValue parseNumber() {
        JsonValue v; v.tag = 2;
        size_t start = i;
        while (i < s.size() && (isdigit((unsigned char)s[i]) || s[i] == '-' || s[i] == '+' || s[i] == '.' || s[i] == 'e' || s[i] == 'E')) i++;
        v.num = stod(s.substr(start, i - start));
        return v;
    }
};

const JsonValue& jsonGet(const JsonValue& obj, const string& key) {
    for (auto& kv : obj.obj) if (kv.first == key) return kv.second;
    static JsonValue null_;
    return null_;
}

int asInt(const JsonValue& v) { return (int)v.num; }
double asDouble(const JsonValue& v) { return v.num; }
bool asBool(const JsonValue& v) { return v.b; }
string asString(const JsonValue& v) { return v.str; }
char asChar(const JsonValue& v) { return v.str.empty() ? '\0' : v.str[0]; }

vector<int> asIntArray(const JsonValue& v) { vector<int> out; for (auto& e : v.arr) out.push_back(asInt(e)); return out; }
vector<double> asDoubleArray(const JsonValue& v) { vector<double> out; for (auto& e : v.arr) out.push_back(asDouble(e)); return out; }
vector<bool> asBoolArray(const JsonValue& v) { vector<bool> out; for (auto& e : v.arr) out.push_back(asBool(e)); return out; }
vector<string> asStringArray(const JsonValue& v) { vector<string> out; for (auto& e : v.arr) out.push_back(asString(e)); return out; }
vector<vector<int>> asIntMatrix(const JsonValue& v) { vector<vector<int>> out; for (auto& row : v.arr) out.push_back(asIntArray(row)); return out; }

string jsonEscape(const string& s) {
    string out;
    for (char c : s) {
        if (c == '\\' || c == '"') { out += '\\'; out += c; }
        else if (c == '\n') { out += "\\n"; }
        else out += c;
    }
    return out;
}

string encodeInt(int v) { return to_string(v); }
string encodeDouble(double v) { ostringstream o; o << setprecision(15) << v; return o.str(); }
string encodeBool(bool v) { return v ? "true" : "false"; }
string encodeString(const string& v) { return "\"" + jsonEscape(v) + "\""; }
string encodeChar(char v) { return "\"" + jsonEscape(string(1, v)) + "\""; }

string encodeIntArray(const vector<int>& v) {
    string out = "[";
    for (size_t k = 0; k < v.size(); k++) { if (k) out += ","; out += to_string(v[k]); }
    return out + "]";
}
string encodeDoubleArray(const vector<double>& v) {
    string out = "[";
    for (size_t k = 0; k < v.size(); k++) { if (k) out += ","; out += encodeDouble(v[k]); }
    return out + "]";
}
string encodeBoolArray(const vector<bool>& v) {
    string out = "[";
    for (size_t k = 0; k < v.size(); k++) { if (k) out += ","; out += (v[k] ? "true" : "false"); }
    return out + "]";
}
string encodeStringArray(const vector<string>& v) {
    string out = "[";
    for (size_t k = 0; k < v.size(); k++) { if (k) out += ","; out += encodeString(v[k]); }
    return out + "]";
}
string encodeIntMatrix(const vector<vector<int>>& v) {
    string out = "[";
    for (size_t k = 0; k < v.size(); k++) { if (k) out += ","; out += encodeIntArray(v[k]); }
    return out + "]";
}
""".strip()


def boilerplate(function_name: str, return_type: str, parameters: list[dict]) -> str:
    params = []
    for p in parameters:
        base = _TYPES[p["type"]]
        params.append(f"{base}& {p['name']}" if p["type"] in _BY_REF else f"{base} {p['name']}")
    ret = _TYPES[return_type]
    return (
        "class Solution {\n"
        "public:\n"
        f"    {ret} {function_name}({', '.join(params)}) {{\n"
        "        \n"
        "    }\n"
        "};\n"
    )


def driver(function_name: str, return_type: str, parameters: list[dict]) -> str:
    lines = [
        "int main() {",
        "    string __input((istreambuf_iterator<char>(cin)), istreambuf_iterator<char>());",
        "    JsonParser __parser(__input);",
        "    JsonValue __root = __parser.parse();",
        '    const JsonValue& __params = jsonGet(__root, "params");',
    ]
    call_args = []
    for idx, p in enumerate(parameters):
        var = f"__p{idx}"
        lines.append(f"    {_TYPES[p['type']]} {var} = {_DECODE[p['type']]}(__params.arr[{idx}]);")
        call_args.append(var)
    lines.append(
        f"    {_TYPES[return_type]} __result = Solution().{function_name}({', '.join(call_args)});"
    )
    lines.append(f"    cout << {_ENCODE[return_type]}(__result) << endl;")
    lines.append("    return 0;")
    lines.append("}")
    return "\n".join(lines)
