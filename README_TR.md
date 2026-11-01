# Binance Futures – Ücretsiz Bulut Sinyal Botu (Demo / Sinyal Modu)

## ÖNEMLİ SINIRLAR
- Bu sürüm **yalnızca sinyal üretir**. Binance hesabına bağlanmaz ve emir göndermez.
- Canlı Binance API adresi veya canlı emir kodu içermez.
- Binance Futures halka açık piyasa verilerini okur; API anahtarı gerekmez.
- GitHub Actions zamanlanmış görevleri gecikebilir. Bu sistem 7/24 düşük gecikmeli bir bot değildir.
- 1.500 USDT sadece risk hesaplama referansıdır; gerçek bakiye okunmaz.
- %1 risk, 15 USDT tahmini risk ve 3x azami nominal pozisyon sınırı hesaplanır. Komisyon, funding, kayma ve likidasyon riski bu tahmini değiştirebilir.
- Rug/honeypot filtreleri merkezi borsadaki USDT-M vadeli işlemlerde token kontrat rug riskini çözmez. Bu bot rug garantisi vermez; haber, likidite, spread ve manipülasyon kontrolleri ayrıca gerekir.

## iPhone push bildirimi (ntfy)
1. iPhone App Store'dan ntfy uygulamasını yükleyin: https://apps.apple.com/app/ntfy/id1625396347
2. Uygulamada `https://ntfy.sh` sunucusunu seçin.
3. Tahmin edilmesi zor, uzun ve rastgele bir konu adı üretin (ör. parola yöneticisiyle 32+ karakter).
4. Bu konuya abone olun ve iPhone Ayarlar > Bildirimler bölümünde ntfy bildirimlerine izin verin.
5. Konu adını kimseyle paylaşmayın. ntfy.sh standart konuları herkese açıktır; konu adını bilen herkes mesajları okuyabilir/gönderebilir. Bildirim metnine hesap bakiyesi veya API bilgisi koymayın.

## GitHub üzerinden ücretsiz zamanlama
1. https://github.com adresinde oturum açın.
2. New repository ile `futures-demo-signals` adında **Public** depo oluşturun (standart GitHub Actions kullanımı ücretsizdir; kodda hiçbir API anahtarı bulunmaz).
3. Bu ZIP içindeki `bot.py`, `README_TR.md` ve `.github/workflows/scan.yml` dosyalarını aynı klasör yapısıyla yükleyin.
   - GitHub web arayüzünde `.github/workflows` klasörlerini oluşturup `scan.yml` dosyasını içine koyun.
4. Depoda Settings > Secrets and variables > Actions > New repository secret yolunu izleyin.
5. Secret name: `NTFY_TOPIC`; Secret value: iPhone'da abone olduğunuz uzun rastgele konu adı.
6. Actions sekmesine gidin. Gerekirse workflow'u etkinleştirin.
7. `Futures Demo Signal Scanner` workflow'unu seçip `Run workflow` ile ilk testi başlatın.
8. Başarılı çalışmada iPhone'a bildirim gelir; bildirim yoksa Actions > run log bölümünü kontrol edin.

## Strateji ve risk ayarları
- Pariteler: BTCUSDT, ETHUSDT, SOLUSDT
- Zaman dilimi: 15 dakika
- Sinyal: EMA20/EMA50 trendi + EMA20 geri-alım/kırılımı + RSI filtresi
- Stop mesafesi: ATR(14) x 1.5 veya fiyatın %0.4'ü (hangisi büyükse)
- Hedef: 2R
- Referans sermaye: 1.500 USDT
- Referans risk: %1 = 15 USDT
- Azami nominal pozisyon: 4.500 USDT (3x varsayımsal üst sınır)

## Canlı veya Demo emirler
Bu paket emir göndermez. Demo emir yürütme, ayrı bir aşamadır ve Binance Futures Demo API anahtarı gerektirir. API anahtarı/secret'ı GitHub'a dosya olarak, sohbete veya koda yazmayın. Gerçek API anahtarını bu projeye eklemeyin.
