from __future__ import annotations

# Modüller arası paylaşılan sabitler. crawler/, core/ ve matching/ birbirini import etmez
# (bkz. CLAUDE.md) ama hepsi AYNI bucket'a yazıp okur — bu isim 4 ayrı dosyada kopyalanmıştı,
# tek doğruluk kaynağı ilkesi gereği buraya toplandı.

DATALAKE_BUCKET = "datalake"

# MinIO'daki her .meta.json sidecar'ına yazılan sürüm — arşivlenmiş ham verinin hangi kod
# sürümüyle çekildiğini geriye dönük ayırt edebilmek için (şema değişirse eski objeler
# hangi mantıkla parse edilmeli sorusunun cevabı).
PLUGIN_VERSION = "0.1.0"
