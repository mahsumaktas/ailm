# Investigation Pipeline

ailm, her sistem olayı için şeffaf ve adım adım soruşturma planı çalıştırır.
Kullanıcı arka planda ne olduğunu her zaman görebilir.

## Tasarım İlkeleri

- Her analiz adımı feed'de görünür (bekliyor / tamam / başarısız / atlandı)
- Kaynaksız bulgu yazılmaz — her veri [VERİ] → [KAYNAK] formatında etiketlenir
- Ölçüm ile yorumun ayrımı her zaman korunur
- Eksik veri açıkça raporlanır ("şu araç başarısız oldu, bu yüzden X bilinemiyor")

## Olay Tipine Göre Sabit Planlar

### Servis Failure

1. journald geçmişi (son 1 saat)
2. Servis dependency'leri
3. Aynı dönemde disk/RAM anomalisi var mı
4. Son güncelleme bu servisi etkiler mi
5. Arch BBS bilinen sorun taraması (v0.3+)

### Disk Alert

1. psutil ile gerçek doluluk ölçümü
2. En büyük dizinleri tespit et (du)
3. Journal log boyutu
4. Büyüme hızı tahmini (son 7 gün trend)
5. Önerilen aksiyon whitelist'ten seç

### Package Update

1. Güncellenen paketleri listele (pacman.log)
2. Snapshot alındı mı kontrol et
3. rebuild-detector çıktısı var mı
4. Bu sürümde bilinen sorun var mı (Arch BBS, v0.3+)
5. Reboot gerekiyor mu (cachyos-reboot-required)

### .pacnew Detected

1. Diff al (mevcut vs pacnew)
2. Fark kaç satır, ne tür değişiklik
3. Son merge ne zaman yapılmış
4. LLM merge önerisi üret
5. Kullanıcıya göster: [Merge] [Mevcut Kalsın] [Diff Göster]

## Evidence Format

Tüm bulgular zorunlu olarak şu formatta raporlanır:

```
[disk %82] → [Kaynak: psutil.disk_usage('/'), 2026-03-26 14:23:01]
[journal 2.3GB] → [Kaynak: du /var/log/journal, 2026-03-26 14:23:02]
[son 7 günde %3 büyüme] → [Kaynak: events tablosu trend analizi]
[vacuum önerisi] → [Kaynak: LLM analizi, input: yukarıdaki 3 veri]
```

## Veri Boşlukları

Her analiz sonunda eksik veri açıkça belirtilir:

```
Örnek:
⚠ Bu analizde eksik veri:
  - Arch BBS araması devre dışı (v0.3'te geliyor)
  - GitHub Advisory API timeout (network hatası)
  Bu olmadan güvenlik değerlendirmesi yapılamadı.
```

## ACH Matrix (v0.4)

Kompleks olaylar için birden fazla hipotez üretilir ve kanıtlarla değerlendirilir.
En az tutarsızlığa sahip hipotez öne çıkar. Kullanıcı reasoning'i takip edebilir.

Esinlenme: OSINT araştırma metodolojisindeki ACH (Analysis of Competing Hypotheses) tekniği.
