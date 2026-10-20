"""OpenAPI docs generated from the registered flask_restful resources.

Paths come from app.url_map, so every endpoint is listed without per-resource decorators.
Served at /swagger.json with the UI at /swagger-ui, only when ENABLE_API_DOCS is on.
"""
import ast
import inspect
import re

from apispec import APISpec
from flask import abort, jsonify
from flask_swagger_ui import get_swaggerui_blueprint

SWAGGER_URL = "/swagger-ui"
SPEC_URL = "/swagger.json"
HTTP_METHODS = ("get", "post", "put", "patch", "delete")

_RULE_ARG = re.compile(r"<(?:(?P<conv>[^:<>]+):)?(?P<name>[^<>]+)>")
_PARAM_TYPES = {
    "int": {"type": "integer"},
    "float": {"type": "number"},
    "uuid": {"type": "string", "format": "uuid"},
}


def obj(properties=None, required=(), **extra):
    """JSON schema for an object body: obj({"phone": STR}, required=["phone"])"""
    schema = {"type": "object", "properties": properties or {}, **extra}
    if required:
        schema["required"] = list(required)
    return schema


STR = {"type": "string"}
INT = {"type": "integer"}
NUM = {"type": "number"}
BOOL = {"type": "boolean"}
MESSAGE = obj({"message": STR})
ERROR_REF = {"$ref": "#/components/schemas/Error"}


def api_doc(
    summary=None,
    description=None,
    body=None,
    responses=None,
    query=None,
    produces=None,
    body_type=None,
):
    """Document one resource method (put it above @auth_required). Plain JSON-schema dicts.

        @api_doc("Sign in", body=obj({"phone": STR, "password": STR}, required=["phone", "password"]),
                 responses={200: ("Signed in", obj({"access_token": STR})), 401: "Wrong phone or password"})

    responses: {status: "description" | ("description", schema)}. Statuses given here replace the
    generic ones. query: {name: (schema, "description")} for query-string parameters. produces: the
    content type of the 200 response when it isn't JSON (a CSV or PDF download). body_type: the request
    content type when it isn't JSON ("multipart/form-data" for uploads).
    """

    def mark(func):
        func._api_doc = {
            "summary": summary,
            "description": description,
            "body": body,
            "responses": responses or {},
            "query": query or {},
            "produces": produces,
            "body_type": body_type,
        }
        return func

    return mark


def _requires_auth(func):
    """True when the method is wrapped by flask_praetorian's @auth_required."""
    while func is not None:
        code = getattr(func, "__code__", None)
        if (
            code is not None
            and "flask_praetorian" in code.co_filename
            and code.co_qualname.startswith("auth_required.")
        ):
            return True
        func = getattr(func, "__wrapped__", None)
    return False


def _const(node):
    return node.value if isinstance(node, ast.Constant) else None


def _is_request_attr(node, attr):
    """request.<attr>"""
    return (
        isinstance(node, ast.Attribute)
        and node.attr == attr
        and isinstance(node.value, ast.Name)
        and node.value.id == "request"
    )


def _is_get_json(node):
    """request.get_json(...) possibly followed by `or {}`"""
    if isinstance(node, ast.BoolOp):
        return any(_is_get_json(v) for v in node.values)
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get_json"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "request"
    )


def _infer(func):
    """Best-effort docs read from a handler's source: query parameters, body fields, the status codes it
    returns (with the first error message for each) and the top-level keys of a JSON success body.

    Only the handler's own body is read, so anything done in a helper is missed. Returns {} when the
    source isn't available.
    """
    try:
        # Parsed under an `if` rather than dedented: dedent leaves the source unparseable when a
        # docstring or string literal has lines indented less than the method
        tree = ast.parse("if 1:\n" + inspect.getsource(inspect.unwrap(func)))
    except (OSError, TypeError, SyntaxError):
        return {}

    query, body, files, form = {}, [], [], []
    aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and _is_get_json(node.value):
            aliases.update(t.id for t in node.targets if isinstance(t, ast.Name))

    def is_body(node):
        return (isinstance(node, ast.Name) and node.id in aliases) or _is_get_json(node)

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.args
        ):
            key, owner = _const(node.args[0]), node.func.value
            if not isinstance(key, str):
                continue
            if node.func.attr == "get" and _is_request_attr(owner, "args"):
                kind = {"type": "string"}
                typ = next((k.value for k in node.keywords if k.arg == "type"), None)
                default = _const(node.args[1]) if len(node.args) > 1 else None
                if isinstance(typ, ast.Name) and typ.id in ("int", "float"):
                    kind = {"type": "integer" if typ.id == "int" else "number"}
                elif isinstance(default, bool):
                    kind = {"type": "boolean"}
                elif isinstance(default, int):
                    kind = {"type": "integer"}
                query[key] = (
                    kind,
                    f"Default {default}" if default not in (None, "") else "",
                )
            elif node.func.attr == "get" and is_body(owner):
                body.append(key)
            elif node.func.attr == "get" and _is_request_attr(owner, "form"):
                form.append(key)
        elif isinstance(node, ast.Subscript):
            key = _const(node.slice)
            if isinstance(key, str) and is_body(node.value):
                body.append(key)
            elif isinstance(key, str) and _is_request_attr(node.value, "files"):
                files.append(key)
            elif isinstance(key, str) and _is_request_attr(node.value, "args"):
                query.setdefault(key, ({"type": "string"}, ""))
        elif isinstance(node, ast.Compare) and isinstance(node.ops[0], ast.In):
            key = _const(node.left)
            if isinstance(key, str) and is_body(node.comparators[0]):
                body.append(key)
            elif isinstance(key, str) and _is_request_attr(
                node.comparators[0], "files"
            ):
                files.append(key)

    responses, produces = {}, None
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Response"
        ):
            mime = next(
                (_const(k.value) for k in node.keywords if k.arg == "mimetype"), None
            )
            produces = produces or mime
        if not isinstance(node, ast.Return) or node.value is None:
            continue
        value, status = node.value, 200
        if isinstance(value, ast.Tuple) and len(value.elts) == 2:
            value, code = value.elts
            status = _const(code)
            if not isinstance(status, int):
                continue
        entry = responses.setdefault(
            status, {"messages": [], "keys": [], "array": False}
        )
        if isinstance(value, ast.Dict):
            keys = [_const(k) for k in value.keys if _const(k) is not None]
            entry["keys"] += [
                k for k in keys if isinstance(k, str) and k not in entry["keys"]
            ]
            for k, v in zip(value.keys, value.values):
                if (
                    _const(k) in ("error", "message")
                    and isinstance(_const(v), str)
                    and _const(v) not in entry["messages"]
                ):
                    entry["messages"].append(_const(v))
        elif isinstance(value, (ast.List, ast.ListComp)):
            entry["array"] = True

    uniq = lambda items: list(dict.fromkeys(items))
    return {
        "query": query,
        "body": uniq(body),
        "files": uniq(files),
        "form": uniq(form),
        "responses": responses,
        "produces": produces,
    }


def _inferred_operation_parts(info, secured):
    """(parameters, requestBody or None, responses) built from _infer() output."""
    parameters = [
        {"name": n, "in": "query", "required": False, "schema": k, "description": d}
        for n, (k, d) in info.get("query", {}).items()
    ]
    request_body = None
    if info.get("files"):
        props = {f: {"type": "string", "format": "binary"} for f in info["files"]}
        props.update({f: {"type": "string"} for f in info["form"]})
        request_body = ("multipart/form-data", obj(props))
    elif info.get("body"):
        request_body = ("application/json", obj({k: {} for k in info["body"]}))

    responses = {}
    for status, entry in sorted(info.get("responses", {}).items()):
        text = " / ".join(entry["messages"][:3]) or (
            "Success" if status < 400 else "Error"
        )
        resp = {"description": text[:300]}
        if status >= 400:
            resp["content"] = {"application/json": {"schema": ERROR_REF}}
        elif entry["array"]:
            resp["content"] = {
                "application/json": {"schema": {"type": "array", "items": {}}}
            }
        elif entry["keys"]:
            resp["content"] = {
                "application/json": {"schema": obj({k: {} for k in entry["keys"]})}
            }
        responses[str(status)] = resp
    if info.get("produces"):
        responses.setdefault("200", {"description": "Success"})["content"] = {
            info["produces"]: {"schema": {"type": "string", "format": "binary"}}
        }
    return parameters, request_body, responses


def _summary(view_class, method, func):
    doc = (func.__doc__ or view_class.__doc__ or "").strip()
    if doc:
        return doc.splitlines()[0].strip()
    words = re.sub(
        r"(?<!^)(?=[A-Z])", " ", view_class.__name__.replace("Resource", "")
    ).strip()
    return f"{method.upper()} {words}"


def _openapi_path(rule):
    """('/admin/users/<int:user_id>') -> ('/admin/users/{user_id}', [parameter objects])"""
    params = []

    def swap(match):
        schema = _PARAM_TYPES.get(match.group("conv"), {"type": "string"})
        params.append(
            {
                "name": match.group("name"),
                "in": "path",
                "required": True,
                "schema": schema,
            }
        )
        return "{%s}" % match.group("name")

    return _RULE_ARG.sub(swap, rule), params


def build_spec(app):
    spec = APISpec(
        title="Tabital API",
        version="1.0.0",
        openapi_version="3.0.3",
        info={
            "description": "Auto-generated from the registered routes. Authorize with the access_token from /login."
        },
    )
    spec.components.security_scheme(
        "bearerAuth", {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
    )
    spec.components.schema(
        "Error",
        {
            "type": "object",
            "properties": {"error": {"type": "string"}, "code": {"type": "string"}},
        },
    )
    error = {
        "description": "Error",
        "content": {
            "application/json": {"schema": {"$ref": "#/components/schemas/Error"}}
        },
    }

    skip = {"static"}
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
        if rule.endpoint in skip or rule.rule.startswith((SWAGGER_URL, SPEC_URL)):
            continue
        view = app.view_functions[rule.endpoint]
        view_class = getattr(view, "view_class", None)
        if view_class is None:
            continue
        path, params = _openapi_path(rule.rule)
        tag = rule.rule.strip("/").split("/")[0] or "root"
        operations = {}
        for method in HTTP_METHODS:
            func = getattr(view_class, method, None)
            if func is None or method.upper() not in rule.methods:
                continue
            secured = _requires_auth(func)
            doc = getattr(func, "_api_doc", None) or {}
            responses = {"200": {"description": "Success"}, "400": error}
            if secured:
                responses.update({"401": error, "403": error})
            for status, resp in doc.get("responses", {}).items():
                text, schema = (
                    resp
                    if isinstance(resp, tuple)
                    else (resp, ERROR_REF if int(status) >= 400 else None)
                )
                responses[str(status)] = {"description": text}
                if schema:
                    responses[str(status)]["content"] = {
                        "application/json": {"schema": schema}
                    }
            inferred = {}
            if not doc.get(
                "responses"
            ):  # explicit responses mean it was documented by hand
                info = _infer(func)
                (
                    inferred_params,
                    inferred_body,
                    inferred_responses,
                ) = _inferred_operation_parts(info, secured)
                if inferred_responses:
                    if not any(code.startswith("2") for code in inferred_responses):
                        inferred_responses["200"] = responses["200"]
                    responses = inferred_responses
                    if secured:
                        responses.setdefault("401", error)
                inferred = {
                    "parameters": [] if doc.get("query") else inferred_params,
                    "body": None if doc.get("body") else inferred_body,
                }
            if doc.get("produces"):
                responses["200"]["content"] = {
                    doc["produces"]: {"schema": {"type": "string", "format": "binary"}}
                }
            op = {
                "summary": doc.get("summary") or _summary(view_class, method, func),
                "operationId": f"{rule.endpoint}_{method}",
                "tags": [tag],
                "responses": responses,
            }
            if doc.get("description"):
                op["description"] = doc["description"]
            parameters = (
                list(params)
                + [
                    {
                        "name": name,
                        "in": "query",
                        "required": False,
                        "schema": schema,
                        "description": text,
                    }
                    for name, (schema, text) in doc.get("query", {}).items()
                ]
                + inferred.get("parameters", [])
            )
            if parameters:
                op["parameters"] = parameters
            if secured:
                op["security"] = [{"bearerAuth": []}]
            if inferred.get("body"):
                content_type, schema = inferred["body"]
                op["requestBody"] = {"content": {content_type: {"schema": schema}}}
            elif method in ("post", "put", "patch"):
                op["requestBody"] = {
                    "content": {
                        doc.get("body_type")
                        or "application/json": {
                            "schema": doc.get("body") or {"type": "object"}
                        }
                    }
                }
            if inferred:
                op[
                    "description"
                ] = "Request and response fields are read from the code, so they may be incomplete."
            operations[method] = op
        if operations:
            spec.path(path=path, operations=operations)
    return spec.to_dict()


def init_docs(app):
    """Call after every route is registered. The spec is built on first request and cached."""
    app.config.setdefault("ENABLE_API_DOCS", False)
    cache = {}

    def require_enabled():
        # if not app.config["ENABLE_API_DOCS"]:
        #     abort(404)
        pass

    @app.route(SPEC_URL, endpoint="api_docs_spec")
    def api_docs_spec():
        require_enabled()
        if "spec" not in cache:
            cache["spec"] = build_spec(app)
        return jsonify(cache["spec"])

    blueprint = get_swaggerui_blueprint(
        SWAGGER_URL, SPEC_URL, config={"app_name": "Tabital API"}
    )
    blueprint.before_request(require_enabled)
    app.register_blueprint(blueprint, url_prefix=SWAGGER_URL)
