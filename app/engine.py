"""Risk sizing using broker P/L, Decimal grids and an explicit all-filled scenario."""
from decimal import Decimal, ROUND_HALF_UP
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict
from .stops import Timeframe


def D(value):
    return Decimal(str(value))


class Plan(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False, extra="forbid")
    symbol: str = Field(min_length=1, max_length=80)
    side: Literal["buy", "sell"] = "buy"
    entry: float = Field(gt=0, le=1e9)
    stop: float = Field(gt=0, le=1e9)
    count: int = Field(ge=1, le=50)
    sizing: Literal["budget", "fixed"] = "budget"
    budget: float = Field(default=60, gt=0, le=1e9)
    lot: float = Field(default=0.01, gt=0, le=1e6)
    commission: float = Field(default=0, ge=0, le=1e6)
    reserve: float = Field(default=0, ge=0, le=1e9)
    sl_mode: Literal['manual','swing','adjusted'] = 'manual'
    timeframe: Timeframe = 'M15'
    buffer_ticks: int = Field(default=0,ge=0,le=10000)
    step_ticks: Literal[1,10,100] = 1
    swing_time: int | None = None
    swing_price: float | None = Field(default=None,gt=0)


def calculate(plan: Plan, broker):
    context = broker.context(plan.symbol)
    spec = context["symbol"]
    tick, minimum, step, maximum = map(D, (spec["tick_size"], spec["volume_min"], spec["volume_step"], spec["volume_max"]))
    if min(tick, minimum, step, maximum) <= 0 or maximum < minimum:
        raise ValueError("Thông số giá/lot của broker không hợp lệ.")
    def price(x):
        return (D(x) / tick).quantize(D(1), rounding=ROUND_HALF_UP) * tick
    first, stop = price(plan.entry), price(plan.stop)
    if (plan.side == "buy" and stop >= first) or (plan.side == "sell" and stop <= first):
        raise ValueError("Buy cần SL dưới entry đầu; Sell cần SL trên entry đầu.")
    entries = [price(first + (stop - first) * D(i) / D(plan.count)) for i in range(plan.count)]
    if len(set(entries)) != len(entries) or stop in entries:
        raise ValueError("Vùng giá quá hẹp: entry bị trùng hoặc chạm SL sau khi làm tròn. Giảm số entry.")
    warnings = list(context.get("warnings", []))
    if first != D(plan.entry) or stop != D(plan.stop):
        warnings.append("Entry đầu/SL đã được làm tròn theo bước giá của broker.")
    # The budget solver assumes linear forex P/L in volume. The broker adapter
    # only exposes forex calculation modes, and every final leg is recalculated.
    base_losses = []
    for entry in entries:
        profit = broker.profit(plan.symbol, plan.side, float(minimum), float(entry), float(stop))
        if profit >= 0:
            raise ValueError("Broker trả P/L không âm tại SL; không thể xác nhận phép tính.")
        base_losses.append(-D(round(profit, 10)))
    unit_cost = sum(base_losses) / minimum + D(plan.count) * D(plan.commission)
    if unit_cost <= 0:
        raise ValueError("Khoảng lỗ quá nhỏ để tính lot tin cậy. Hãy kiểm tra thông số symbol.")
    minimum_risk = unit_cost * minimum + D(plan.reserve)
    feasible = True
    if plan.sizing == "budget":
        available = D(plan.budget) - D(plan.reserve)
        raw_lot = max(D(0), available / unit_cost)
        if raw_lot < minimum:
            feasible = False
            volume = minimum
            warnings.append("Ngay cả lot tối thiểu cũng vượt ngân sách. Không có phương án phù hợp.")
        else:
            volume = minimum + ((min(raw_lot, maximum) - minimum) // step) * step
            if raw_lot > maximum:
                warnings.append("Lot mỗi entry đã giới hạn ở mức tối đa của broker.")
    else:
        volume = D(plan.lot)
        if volume < minimum or volume > maximum or (volume - minimum) % step != 0:
            raise ValueError(f"Lot phải từ {minimum} đến {maximum}, theo bước {step} kể từ {minimum}.")
    def totals(vol):
        rows = []
        for index, entry in enumerate(entries):
            pnl = D(round(broker.profit(plan.symbol, plan.side, float(vol), float(entry), float(stop)), 10))
            if pnl >= 0:
                raise ValueError("Kết quả P/L không nhất quán. Hãy tính lại.")
            fee = vol * D(plan.commission)
            rows.append({"index": index + 1, "entry": float(entry), "lot": float(vol), "price_loss": float(-pnl), "commission": float(fee), "loss": float(-pnl + fee)})
        return rows, sum(D(r["loss"]) for r in rows) + D(plan.reserve)
    rows, total = totals(volume)
    # Repricing may change conversion rates while the solver runs. Recheck a
    # bounded number of times and fail closed rather than oversize silently.
    for _ in range(4):
        if plan.sizing != "budget" or not feasible or total <= D(plan.budget):
            break
        if volume - step < minimum:
            feasible = False
            warnings.append("Lot tối thiểu vượt ngân sách theo báo giá mới nhất.")
            break
        volume -= step
        rows, total = totals(volume)
    if plan.sizing == "budget" and feasible and total > D(plan.budget):
        raise ValueError("Tỷ giá biến động trong lúc tính. Vui lòng thử lại.")
    broker.verify_account(context["account"])
    total_lot = volume * D(plan.count)
    equity = D(context["account"]["equity"])
    return {"context": context, "plan": plan.model_dump(), "entries": rows, "stop": float(stop),
            "lot_each": float(volume), "total_lot": float(total_lot), "average_entry": float(sum(entries) / D(plan.count)),
            "price_loss": sum(r["price_loss"] for r in rows), "commission": sum(r["commission"] for r in rows),
            "reserve": plan.reserve, "total_loss": float(total), "minimum_risk": float(minimum_risk),
            "remaining": float(D(plan.budget) - total), "risk_percent": float(total / equity * 100) if equity > 0 else None,
            "feasible": feasible, "within_budget": total <= D(plan.budget), "warnings": warnings}
