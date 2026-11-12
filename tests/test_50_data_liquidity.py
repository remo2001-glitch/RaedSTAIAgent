"""الشموع اليومية UTC (الدفعة 72) ولقطة الدفتر (الدفعة 73)."""
from _util import chk, src
import core.data_layer as DL
import core.indicators as IND
import core.microstructure as MS

chk("U1 شموع OKX اليومية UTC", DL.OKX_DAILY_BAR == "1Dutc" and DL.DAILY_OPEN_HOUR_UTC == 0)
d = src("core/data_layer.py")
chk("U2 لا bar=1D بنظام هونغ كونغ في الطلبات", "bar=1D&" not in d and 'bar="1D"' not in d)
chk("U3 مفتاح لقطات Redis الجديد", "raed:ohlcvsnap:u:" in d)
chk("U4 وسم يوم UTC", "9 نوفمبر" in IND.volume_candle_label_ar("2026-11-09"))
bids = [[74000, 0.0005]] * 20
asks = [[74100, 0.3]] * 20
chk("Q1 كشف لقطة مشوّهة", MS._book_anomalous_b73(bids, asks) is True)
chk("Q2 لقطة طبيعية", MS._book_anomalous_b73([[74000, 0.3]] * 20, asks) is False)
chk("Q3 أصل صغير لا يُحكم عليه", MS._book_anomalous_b73([[1, 1]] * 20, [[1, 1000]] * 20) is False)
chk("Q4 حقل book_anomaly", "book_anomaly" in MS.LiquidityProfile.__dataclass_fields__)
