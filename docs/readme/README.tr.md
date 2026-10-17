Translated from README.md at commit 59b1150. The English README is authoritative for numbers.

<div align="center">

[English](../../README.md) · [简体中文](README.zh-CN.md) · [繁體中文](README.zh-TW.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Español](README.es.md) · [Português (Brasil)](README.pt-BR.md) · [Français](README.fr.md) · [Deutsch](README.de.md) · [Русский](README.ru.md) · **Türkçe** · [Tiếng Việt](README.vi.md) · [Bahasa Indonesia](README.id.md) · [हिन्दी](README.hi.md)

# HillClimbAnything

### Herhangi bir teknik zorlukta hill-climb yapmanızı sağlayan harness.

**Kodlama ajanınızı bir sayıya yöneltin. Yalnızca gerçek kazanımları tutun.**

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776ab?style=flat-square&logo=python&logoColor=white)](../../README.md#quick-start)
[![Zero dependencies](https://img.shields.io/badge/dependencies-0-2a78d6?style=flat-square)](../../pyproject.toml)
[![Tests](https://img.shields.io/badge/tests-327_passing-1baf7a?style=flat-square)](../../tests)
[![Agent skill](https://img.shields.io/badge/agent_skill-SKILL.md-5c4ee5?style=flat-square)](../../skills/hca/SKILL.md)
[![MIT license](https://img.shields.io/badge/license-MIT-f4c430?style=flat-square)](../../LICENSE)

<code>uvx --from hillclimbanything hca quick -- python bench.py</code>

<sub>Hiçbir şey kurmadan deneyin: kıyaslamanız ne kadar gürültülü ve hangi kazancı tespit edebilirsiniz?</sub>

<br /><br />

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/climb-dark.svg" />
  <img src="../assets/climb-light.svg" alt="18 denemede Python-Markdown render süresi: tutulan değişiklikler süreyi adım adım düşürüyor; ayrılmış (held-out) belgeler üzerindeki dört confirm -18.9, -45.9, -49.8 ve -53.8 yüzdede geçiyor; 9. deneme tek bir çalıştırmada -17.9 yüzde okundu, ancak 20 blokta -1.7 yüzde çıktı ve geri alındı" width="900" />
</picture>

</div>

---

Autoresearch tarzı döngüler, bir ajanın kodu düzenlemesine, ölçmesine ve en iyi skoru getireni tutmasına izin verir. Gürültülü bir metrikte "en iyi" çoğu zaman şanstır. Sıradan bir masaüstü bilgisayarda, ham en iyi skoru tutmak, hiçbir işe yaramayan değişikliklerin **%17–55**'ini tuttu; oran benchmark'a göre değişiyor ([ölçüm](../../spikes/phase0/RESULT.md)).

HillClimbAnything bu döngünün **hakemidir**:

- başlamadan önce sayınızın ne kadar gürültülü olduğunu ölçer;
- bir değişikliği yalnızca gürültüyü aşarsa tutar;
- toplam kazancı, ajanın hiç ayar yapmadığı veriler üzerinde doğrular.

Ajanınız öneri getirir, HCA karar verir. HCA, bağımlılığı olmayan küçük bir Python CLI'ıdır; yanında, bir kodlama ajanına yöntemi öğreten bir skill (düz Markdown) bulunur. Claude Code ile test edilmiştir.

## Hızlı başlangıç

**1. Kurulum** (Python 3.11+; Linux, WSL ve yerel Windows'ta test edildi, macOS test edilmedi):

```bash
uv tool install hillclimbanything   # ya da: pipx install hillclimbanything
hca setup                                                        # ajan skill'ini ekler (önce planını gösterir)
```

`hca setup`, skill'i ajanların aradığı yere koyar: `~/.claude/skills/hca` (Claude Code) ve
`~/.agents/skills/hca` (Codex, Cursor, Gemini CLI, Copilot). Değiştirdiği her şeyin yedeğini alır,
ve `hca setup --uninstall` onu yeniden kaldırır. [`uv`](https://docs.astral.sh/uv/getting-started/installation/) ya da [`pipx`](https://pipx.pypa.io/stable/installation/) yok mu? Düz
`pip install` bir sanal ortam içinde çalışır; birçok sistem ortam dışında reddeder (PEP 668).

<details>
<summary>Skill'i eklemenin diğer yolları</summary>

**Claude Code eklentisi:**

```text
/plugin marketplace add JaiS1/HillClimbAnything
/plugin install hca@hillclimbanything
```

**[skills.sh](https://github.com/vercel-labs/skills):**

```bash
npx skills add JaiS1/HillClimbAnything --skill hca
```

Hangisini seçerseniz seçin skill `hca` CLI'ını yönetir, bu yüzden CLI'ı da kurun.
</details>

**2. Ajanınıza söyleyin:**

```text
hca skill'ini (HillClimbAnything) kullanarak `python bench.py` komutunu hızlandır: önce `hca quick` çalıştır,
hca.toml dosyasını benimle birlikte yaz, `hca try` ile tırman ve yalnızca `hca confirm`'ün geçirdiklerini raporla.
```

**3. Ya da kendiniz kullanın:**

<div align="center">
<img src="../assets/demo-terminal.svg" alt="Terminal: hca quick yüzde 0.6 gürültü bildiriyor; hca try yüzde -28.6'lık bir değişikliği tutuyor; başka bir deneme, tek bir çalıştırmada yüzde -17.9 okunmuş olmasına rağmen geri alınıyor; hca confirm yüzde -53.8'de geçiyor" width="900" />
</div>

```bash
hca compare -- <A> -- <B>   # hızlı bir komut için dakikalar içinde ilk sonuç: B gerçekten A'dan iyi mi? git yok, hca.toml yok
hca quick -- <cmd>     # 2 dakika, kurulum yok: bu sayı ne kadar gürültülü, hangi kazancı tespit edebiliriz?
hca init               # evaluator'ı dondur, bir branch oluştur, başlangıç gürültüsünü ölç
hca try -m "why"       # düzenlemeni son keep'e karşı ölç → keep (commit) ya da revert
hca confirm            # toplam kazancı, ayrılmış (held-out) veride orijinale karşı yeniden ölç
hca report             # her deneme, kararı ve kanıtı
```

`hca compare` ne repo ne de config ister: iki komutu rastgele sıralı A/B bloklarında çalıştırır ve farkı aralık ve p-değeriyle, gördüğü noise'u ve tespit edebileceği en küçük farkı raporlar; böylece "tespit edilebilir fark yok" bir şey ifade eder. Exit code 0, B'nin A'dan anlamlı ölçüde iyi olduğu anlamına gelir; 1 değil; 2 bir run'ın fail olduğu veya metriği vermediği. Komutlar shell olmadan çalışır (gerekirse `sh -c '...'` yazın); varsayılan metrik wall time'dır, `--metric NAME` verirseniz `METRIC NAME=value` satırı okunur. Kendi kodunuzda korumalı bir tırmanış isterseniz sıradaki adım `hca init`. Genellikle 20 bloğun tamamını çalıştırır (10'da yalnızca ilk bakış zaten kesinse durur). Exit code'lar: 0 B daha iyi, 1 değil, 2 geçersiz, 3 kullanım hatası, 4 dahili hata. Native Windows'ta shell built-in'leri ve `npm` ya da `dir` gibi `.cmd` shim'leri `cmd /c ...` ister. A komutu `--` içeremez; başka bir ayraç için `--sep TOKEN` kullanın.

Bir görev; bir brief, ajanın düzenleyebileceği yollar ve sayıyı yazdıran tek bir komuttan oluşur:

```toml
# hca.toml
[task]
name     = "api-latency"
editable = ["src/**"]          # ajan bunları değiştirebilir
frozen   = ["bench/**"]        # evaluator: hash'lenir, asla düzenlenmez

[objective]
command       = ["python3", "bench/run.py"]   # METRIC p95_ms=212.4 yazdırır
metric        = "p95_ms"
direction     = "min"
useful_effect = "5%"           # tutmaya değer en küçük kazanç
```

## Showcase: Python-Markdown, 2× daha hızlı

Bir Sonnet kodlama ajanı, HCA'yı hakem olarak kullanarak [Python-Markdown](https://github.com/Python-Markdown/markdown) 3.11.1 üzerinde tırmandı: yaklaşık 80 dakikada 18 deneme, 14'ü tutuldu.

| Kontrol | Sonuç |
|---|---|
| Ayrılmış (held-out) belgelerde `hca confirm` | render süresi **−53.8%** (en az −53.0%) |
| HCA dışında, projenin test fixture'ları üzerinde bağımsız yeniden ölçüm | **−63.5%** |
| Çıktı: projenin test paketi (1,126 test; 3.11.1'de olduğu gibi 110'u atlandı) ve 23,000 rastgele belge | 3.11.1 ile **aynı** |
| Tek çalıştırmalı bir döngünün tutacağı, ama HCA'nın reddettiği değişiklikler | **4'te 3**; biri "−17.9%" görünürken gerçekte −1.7% çıktı |

En büyük kazanç, metin, her eşleşmenin gerektirdiği bir karakteri içeremiyorsa inline regex desenlerini atlamaktan geldi. [Yazı, patch, deneme başına log ve ham ledger](../../showcases/python-markdown/README.md) bağlantısından okuyabilirsiniz. Tek bir makine ve tek bir çalıştırmadır; patch upstream tarafından incelenmedi.

## Showcase: js-yaml, %15 daha hızlı

İkinci tırmanış: başka bir dilde ve farklı türde bir metrikle. Node üzerinde [js-yaml](https://github.com/nodeca/js-yaml) 5.5.0; 491 gerçek GitHub Actions, Kubernetes, Helm, Compose ve OpenAPI dosyasında her belge için `load()` bir kez ölçüldü. Bir kodlama ajanı yaklaşık bir saatte 12 deneme yaptı, 3'ü tutuldu.

<div align="center">
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="../assets/climb-yaml-dark.svg" />
  <img src="../assets/climb-yaml-light.svg" alt="js-yaml `load()` süresi, 12 denemede: tutulan üç değişiklik onu adım adım düşürüyor; ayrılmış belgelerde tek bir confirm -15.3 yüzde ile geçiyor" width="900" />
</picture>
</div>

| Kontrol | Sonuç |
|---|---|
| Ayrılmış (held-out) belgelerde `hca confirm` (aynı türler, çoğunlukla farklı dosyalar) | `load()` süresi, öğe başına medyan **−15.3%** (en az −13.3%) |
| Temiz klonda, orijinal sürüme karşı A/B, 200 yeni süreç | toplam süre **−15.3%** (%95 GA: −16.6% ile −14.4%) |
| Çıktı: js-yaml'in kendi 1,633 testi, ESM ve CJS derlemelerinin her birinde 25,384 karşılaştırma, 11,250 fuzz karşılaştırması | 5.5.0'dan **0 fark** |

Kazanımlar, ayrıştırıcıda işten tasarruf eden küçük değişikliklerdir: düğüm başına daha az ayırma, ilk karaktere göre yönlendirme ve düz skalerler için hızlı yol. Tutulan üç denemenin ikisi, tek başına tutma eşiğinin altında kalan değişiklikleri birleştiriyor; yazıda bu açıkça belirtiliyor, confirm ise son durumu kapsıyor. Metrik, çoğunlukla küçük belgelerde çağrı başına, görece soğuk süredir; sıcak döngüde verim değildir. Tek bir makine söz konusu ve patch upstream'e gönderilmedi. [Yazı, patch, ledger ve harness](../../showcases/js-yaml/README.md) bağlantısından okuyabilirsiniz.

## Neden bir hakem?

İki özdeş build, çoğu döngünün karşılaştırdığı biçimde karşılaştırıldı ([Phase 0](../../spikes/phase0/RESULT.md)):

| Tutma kuralı | Tutulan etkisiz değişiklikler | Tutulan gerçek %5 hızlanmalar |
|---|---|---|
| Ham en iyi skoru tut | **17–55%** | 100% |
| HCA'nın rastgele bloklar üzerindeki testi | **≤ 1%** | 76–96% (15 blok) |

Aralıklar iki benchmark'ı kapsar (bir web sunucusu ve bir CLI). HCA satırı, α = 1% ile tek bir bakıştır; gerçekte kullanılan kural daha katıdır.

Etkisiz değişikliklerin beşte birinden yarısına kadarını tutan bir döngü gürültüye tırmanıyordur. Kazançları üst üste yığılmış görünür, sonra yeniden ölçtüğünüzde buharlaşır.

## Nasıl çalışır

```mermaid
flowchart LR
    Q["hca quick<br/>noise + detectable effect"] --> I["hca init<br/>freeze evaluator, baseline A/A"]
    I --> E["agent edits<br/>editable paths only"]
    E --> T{"hca try<br/>10–20 randomised blocks"}
    T -- "beats the noise<br/>and ≥ ½ useful effect" --> K["keep<br/>(commit)"]
    T -- "otherwise" --> R["revert<br/>(patch kept in ledger)"]
    K --> E
    R --> E
    K -. "every few keeps" .-> C{"hca confirm<br/>held-out, vs original"}
    C -- "pass" --> E
    C -- "fail" --> B["roll back to<br/>last confirmed"]
```

- **Rastgele bloklar.** Her deneme, baseline ve adayı art arda, rastgele sırayla 10 ila 20 kez çalıştırır. Rastgele sıra, gürültü nasıl görünürse görünsün (ağır kuyruklar, termal sıçramalar, arka planda açık bir oyun) kesin bir sign-flip testini geçerli kılar.
- **Kanıt ve büyüklük.** Bir keep için, tek yönlü testin deneme başına %1'de geçmesi ve tahminin, faydalı etkinizin en az yarısına ulaşması gerekir.
- **Özdeş kollar.** Her iki kol da tam commit'lerin temiz git worktree'leridir. Tutulan commit, ölçülen ağacın aynısıdır.
- **Confirm yoksa olmamıştır.** `hca confirm` geçmişi kontrol eder, ardından toplam kazancı orijinale karşı yeniden ölçer. Ajan yalnızca PASS veya FAIL görür.
- **Her şey diskte.** Her denemenin patch'i, ham çalıştırmaları, kararı ve gerekçeleri `.hca/` içindedir.

## Keşif modu (deneysel, isteğe bağlı)

Yukarıdaki her şey *çizgi modu*dur: her aday son keep'e karşı test edilir. Keşif modu, keep çıtasını düşürmeden fikirler için bir bellek ve aday programlardan oluşan bir ağaç ekler.

- **Fikirler.** Ajan hipotezlerini `IDEAS.md` içinde tutar (`## I4 [avoid-work] Skip absent patterns`). `hca try --idea I4` bir denemenin hangi fikri test ettiğini kaydeder, `hca next` de her fikrin sonucunu listeler.
- **Eleme (screen).** `hca try --explore` bir çocuğu, son keep olması gerekmeyen *ebeveynine* karşı 5–8 blokta ölçer. Eleme çocuğu sıralar ve arşivler. Hiçbir zaman bir şeyi keep edemez ve kanıt değildir.
- **Arşiv.** Dokunulan her modül için bir elit tutulur; böylece başta daha kötü görünen bir basamak taşı, iyileşecek kadar uzun hayatta kalır. Her elemeden sonra HCA bir sonraki ebeveyni örnekler (en az %40 oranında son keep) ve düzenlenebilir yolları ona ayarlar.
- **Terfi.** `hca try --promote M`, M üyesini taze bloklarda, normal bir deneme olarak son keep'e karşı test eder; bu yüzden M nasıl seçilmiş olursa olsun test başına %1 yanlış keep oranı korunur. `hca confirm` eskisi gibi çalışır; başarısız bir confirm, geri aldığı keep'lerin üzerine kurulmuş her üyeyi tainted olarak işaretler.
- **`hca next`** bütçeyi, ebeveyni ve soy çizgisini, fikirleri, arşivi ve bir terfi ipucunu en fazla 60 satırda gösterir. Bir sürüm boyunca daha kalacak olan `hca status` komutunun yerini alır.

Bu henüz kanıtlanmadı. Gerçek hedeflerde bir aday popülasyonunun tek bir çizgiden daha iyi tırmanıp tırmanmadığını [tasarım belgesindeki](../DESIGN-climb-engine.md) kanıt tırmanışları ölçecek. Onlar çalışana kadar varsayılan çizgi modudur ve yukarıdaki iki showcase de çizgi modunda tırmanıldı. Keşif modu yalnızca `blocks` modunda çalışır. Kaydedilen bir eleme 0 ile, geçersiz bir eleme 2 ile çıkar.

## Neyi tırmanabilirsiniz

Bir komutun sayıya çevirebildiği her şey; yeter ki sayı, gürültüsünü ölçecek kadar tekrarlanabilir olsun.

| Hedef | Örnek metrik | Durum |
|---|---|---|
| Kütüphane hot path'i | render / parse süresi | ✅ showcase var (Python-Markdown, −53.8%; js-yaml, −15.3%) |
| CLI ve build süresi | duvar saati saniyesi | 🧪 kalibre edildi ([Phase 0](../../spikes/phase0/RESULT.md)) |
| Web sunucusu gecikmesi | route başına ilk bayta kadar süre | 🧪 kalibre edildi ([Phase 0](../../spikes/phase0/RESULT.md)) |
| Tarayıcıda sayfa yüklenmesi | sayfa başına LCP | 🗺️ planlanan showcase |
| UI veya oyun kare süresi | sahne başına p99 kare ms | 🗺️ planlanan showcase |
| Model veya prompt doğruluğu | öğe başına eval skoru | 🧪 `items` modu, unit test'li |
| Ücretli GPU eğitim çalıştırmaları | eğitim sonrası eval skoru | 📋 kampanya yöntemi [skill referanslarında](../../skills/hca/references/campaign-card.md); araçlar planlandı |

## Ajanlar için

Bunu okuyan bir yapay zekâ ajanıysanız:

- **Yöntem:** [`skills/hca/SKILL.md`](../../skills/hca/SKILL.md). Önkoşul kontrollerini, deneme başına tek mekanizmayı ve dondurulmuş dosyalara asla dokunmamayı kapsar.
- **Spec ve çıktı sözleşmesi:** [`skills/hca/references/spec.md`](../../skills/hca/references/spec.md).
- **Crawler'lar ve asistanlar için genel bakış:** [`llms.txt`](../../llms.txt).
- **Bu repo üzerinde çalışmak:** [`AGENTS.md`](../../AGENTS.md).

| Çıkış kodu | `hca try` | `hca confirm` |
|---|---|---|
| 0 | tutuldu (`keep`) | geçti |
| 1 | geri alındı (`revert`) | başarısız (geri alındı) |
| 2 | geçersiz / void / reddedildi: hiçbir şey tutulmadı, düzenlemeleriniz yerinde bırakıldı | sonuçsuz veya reddedildi |
| 3 / 4 | kullanım hatası / dahili hata | aynı |

Yalnızca `hca confirm`'ün geçirdiğini, sınırı ve deneme sayısıyla birlikte raporlayın.

## SSS

<details>
<summary><strong>HCA bir LLM çağırıyor mu, API anahtarı gerekiyor mu?</strong></summary>

Hayır. HCA hiçbir zaman bir model çağırmaz. Düşünme işini ajanınız (Claude Code, Codex, Cursor veya başka biri) yapar; HCA yalnızca ölçer ve karar verir. `hca quick` hiçbir ajan olmadan da çalışır.
</details>

<details>
<summary><strong>Bir deneme ne kadar sürer?</strong></summary>

Komutunuzun yaklaşık 20 ila 40 çalıştırması: 10 blok, sonuç umut verici görünürse 20'ye uzatılır, blok başına iki çalıştırma. 3 saniyelik bir benchmark'ta deneme başına 1 ila 2 dakika sürer. `hca quick` maliyeti başlamadan önce söyler.
</details>

<details>
<summary><strong>Metriğim gürültülü. Bu işe yarar mı?</strong></summary>

`hca quick` bunu yaklaşık 2 dakikada yanıtlar. Gürültüyü ve tespit edebileceğiniz en küçük kazancı bildirir. Yanıt "burada çok gürültülü" ise en ucuz çözümü söyler: çalıştırma başına daha fazla öğe, daha uzun çalıştırmalar ya da diğer yükü kapatmak. Bundan önce tırmanmak boşa emektir.
</details>

<details>
<summary><strong>Ajan hile yapabilir mi?</strong></summary>

Deneyebilir; HCA bunu zahmetli hale getirir:

- dondurulmuş evaluator dosyaları hash'lenir;
- bildirilen yolların dışındaki düzenlemeler denemeyi geçersiz kılar;
- iki kol da temiz worktree'lerden çalışır;
- çöken çalıştırmalar en kötü değer olarak puanlanır;
- tekrarlanan denemeler sayılır.

Bu bir güvenlik sınırı değildir, bu yüzden patch'leri okuyun. [docs/LIMITS.md](../LIMITS.md) bilinen tüm boşlukları ve bunlar için ne yapılacağını listeler.
</details>

<details>
<summary><strong>Bu, autoresearch'ten nasıl ayrılıyor?</strong></summary>

[autoresearch](https://github.com/karpathy/autoresearch), döngü içindeki bir ajanın ne kadar verimli olabileceğini gösterdi; HCA da bu döngüyü korur. HCA'nın eklediği şey karardır: herhangi bir komut ve metrik için bir değişikliğin gerçek mi yoksa gürültü mü olduğu, bir de kazananın laneti (winner's curse) karşısında koruyan bir confirm adımı.
</details>

<details>
<summary><strong>GPU eğitimi gibi pahalı çalıştırmalar ne olacak?</strong></summary>

Skill'in referansları, aylar süren gerçek bir model fine-tuning kampanyasından edinilen disiplini taşır:

- eşleştirilmiş bir "aynı hesaplama, fikir yok" kontrolü içeren, önceden kayda geçirilmiş (preregistered) bir kart;
- ücretli çalıştırmadan önce ucuz bir prova;
- adversarial review;
- uzak işin içinde watcher'lar ve bütçe sayaçları;
- herhangi bir karardan önce derin bir değerlendirme.

Bunun araçları planlanıyor.
</details>

<details>
<summary><strong>Windows?</strong></summary>

Yerel Windows çalışır (Python 3.11+). Zaman aşımında tüm süreç ağacı bir Job Object ve `taskkill` ile sonlandırılır. Kabuk yoktur, bu yüzden komutları argv listesi olarak yazın (bir batch dosyası `["cmd", "/c", "build.bat"]` olur). En sıkı süreç yalıtımını WSL vermeye devam eder, macOS test edilmedi.
</details>

## Dokümantasyon

- [Showcase: Python-Markdown](../../showcases/python-markdown/README.md)
- [Showcase: js-yaml](../../showcases/js-yaml/README.md)
- [Sınırlar ve bilinen boşluklar](../LIMITS.md)
- [Phase 0 kalibrasyonu](../../spikes/phase0/RESULT.md)
- [Tasarım](../DESIGN-hca.md)
- [Mimari sözleşme](../ARCHITECTURE-v1.md)

## Durum ve katkılar

Erken araştırma sürümü (0.2.0.dev): çekirdek döngü ve iki showcase. HCA, eşleştirilmiş istatistiklerini koruduğu [Dream Loop](https://github.com/JaiS1/dream-loop/tree/dream-loop-v0.1)'tan doğdu. [autoresearch](https://github.com/karpathy/autoresearch) tarzı döngülerden ve [Harness Learning](https://arxiv.org/abs/2609.35738) makalesinin değerlendirme hijyeninden fikirler ödünç alır.

```bash
python3 -m unittest discover -s tests      # geliştirme
```

[MIT Lisansı](../../LICENSE). HCA sizi sahte bir kazancı yayına almaktan kurtardıysa, bir yıldız başkalarının onu bulmasına yardımcı olur.
