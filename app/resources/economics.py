"""Phase 7: founder / admin reporting on unit economics, portfolio health, cohorts, liquidity."""
import csv
import io
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from flask import Response
from flask_praetorian import auth_required, current_user
from flask_restful import Resource, request

from ..models.system_settings import SystemSetting
from ..services import economics
from ..services.plan_engine import PlanError


def _json(value):
    """Decimals -> floats (the API's JSON contract), recursively."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json(v) for v in value]
    return value


def _admin():
    return current_user().role == "admin"


def _dates():
    today = datetime.utcnow().date()
    try:
        end = (
            datetime.strptime(request.args["to"], "%Y-%m-%d").date()
            if request.args.get("to")
            else today
        )
        start = (
            datetime.strptime(request.args["from"], "%Y-%m-%d").date()
            if request.args.get("from")
            else date(end.year, 1, 1)
        )
    except ValueError:
        return None, None
    return start, end


class AdminEconomicsSummaryResource(Resource):
    @auth_required
    def get(self):
        if not _admin():
            return {"error": "Unauthorized"}, 403
        start, end = _dates()
        if not start or start > end:
            return {"error": "Use from/to as YYYY-MM-DD, with from before to"}, 400
        return _json(economics.summary(start, end)), 200


class AdminEconomicsExportResource(Resource):
    @auth_required
    def get(self):
        if not _admin():
            return {"error": "Unauthorized"}, 403
        start, end = _dates()
        if not start or start > end:
            return {"error": "Use from/to as YYYY-MM-DD, with from before to"}, 400
        data = economics.summary(start, end)
        out = io.StringIO()
        w = csv.writer(out)
        cost_keys = list(economics.RATES)
        w.writerow(
            [
                "Month",
                "Plans",
                "GMV (GHS)",
                "Financed (GHS)",
                "Average ticket",
                "Merchant fee revenue",
            ]
            + [economics.RATES[k][2] for k in cost_keys]
            + ["Modelled net", "Margin % of GMV"]
        )
        for m in data["by_month"] + [{"month": "TOTAL", **data["totals"]}]:
            w.writerow(
                [
                    m["month"],
                    m["plans"],
                    f'{m["gmv"]:.2f}',
                    f'{m["financed"]:.2f}',
                    f'{m["average_ticket"]:.2f}',
                    f'{m["merchant_fee_revenue"]:.2f}',
                ]
                + [f'{m["costs"][k]:.2f}' for k in cost_keys]
                + [f'{m["modelled_net"]:.2f}', f'{m["modelled_margin_pct_of_gmv"]:.2f}']
            )
        t = data["totals"]
        w.writerow([])
        w.writerow(["Late fee revenue (actual)", f'{t["late_fee_revenue"]:.2f}'])
        w.writerow(
            ["Deferment fee revenue (actual)", f'{t["deferment_fee_revenue"]:.2f}']
        )
        w.writerow(
            ["Credit losses booked (actual)", f'{t["actual_credit_losses"]:.2f}']
        )
        w.writerow(["Net with actuals", f'{t["net_with_actuals"]:.2f}'])
        for warning in data["warnings"]:
            w.writerow(["Note", warning])
        return Response(
            out.getvalue(),
            mimetype="text/csv",
            headers={
                "Content-Disposition": f"attachment; filename=tabital_unit_economics_{start:%Y%m%d}_{end:%Y%m%d}.csv"
            },
        )


class AdminEconomicsPortfolioResource(Resource):
    @auth_required
    def get(self):
        if not _admin():
            return {"error": "Unauthorized"}, 403
        return _json(economics.portfolio()), 200


class AdminEconomicsCohortsResource(Resource):
    @auth_required
    def get(self):
        if not _admin():
            return {"error": "Unauthorized"}, 403
        return _json(economics.cohorts()), 200


class AdminEconomicsScenarioResource(Resource):
    @auth_required
    def get(self):
        """What-if: margin on one product/plan with current (or trial) rates. Nothing is saved."""
        if not _admin():
            return {"error": "Unauthorized"}, 403
        a = request.args
        try:
            price = Decimal(a.get("price", "4000"))
            n = int(a.get("n", 4))
            default_dpr = (
                SystemSetting.get_value("down_payment_percentage", 40)
                if n == 4
                else SystemSetting.get_value("down_payment_percentage_short_plans", 50)
            )
            dpr = Decimal(a.get("dp_percentage", default_dpr)) / 100
            mdr = (
                Decimal(
                    a.get(
                        "mdr_percentage",
                        SystemSetting.get_value("merchant_fee_percentage", 10),
                    )
                )
                / 100
            )
            trial = {k: a.get(k) for k in economics.RATES if a.get(k) not in (None, "")}
            r = economics.rates(trial)
            result = economics.scenario(price, n, dpr, mdr, r)
        except (InvalidOperation, ValueError, TypeError, PlanError) as e:
            return {"error": str(e) or "Check the numbers"}, 400
        return (
            _json(
                {
                    **result,
                    "rates": r,
                    "warnings": economics.rate_warnings(r),
                    "dp_percentage": dpr * 100,
                    "mdr_percentage": mdr * 100,
                }
            ),
            200,
        )
