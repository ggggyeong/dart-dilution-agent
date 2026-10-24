from datetime import date, datetime

from pydantic import BaseModel


class B001Filing(BaseModel):
    """b001_filings 표 한 줄 (OpenDART list.json 항목 그대로, 거르지 않음)."""

    rcept_no: str
    corp_code: str
    corp_name: str
    stock_code: str | None
    corp_cls: str
    report_nm: str
    rcept_dt: date

    @classmethod
    def from_list_item(cls, item: dict) -> "B001Filing":
        return cls(
            rcept_no=item["rcept_no"],
            corp_code=item["corp_code"],
            corp_name=item["corp_name"],
            stock_code=(item.get("stock_code") or "").strip() or None,
            corp_cls=item["corp_cls"],
            report_nm=item["report_nm"],
            rcept_dt=datetime.strptime(item["rcept_dt"], "%Y%m%d").date(),
        )
