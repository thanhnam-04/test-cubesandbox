from pathlib import Path

html = r"""<!DOCTYPE html>
<html lang="vi">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Modern HTML Slide Deck</title>
  <style>
    :root{
      --bg:#08111f;
      --text:#f8fafc;
      --muted:#a8b3c7;
      --blue:#6ea8ff;
      --purple:#b889ff;
      --pink:#ff85b7;
      --card:rgba(255,255,255,.085);
      --border:rgba(255,255,255,.14);
    }

    *{box-sizing:border-box;margin:0;padding:0}
    html,body{width:100%;height:100%}

    body{
      font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
      background:#020617;
      color:var(--text);
      overflow:hidden;
    }

    .deck{
      position:relative;
      width:100vw;
      height:100vh;
    }

    .slide{
      position:absolute;
      inset:0;
      display:none;
      overflow:hidden;
      padding:6.5vh 6vw;
      background:
        radial-gradient(circle at 82% 14%, rgba(65,105,225,.30), transparent 27%),
        radial-gradient(circle at 10% 85%, rgba(162,89,255,.25), transparent 30%),
        linear-gradient(135deg,#0a1324 0%,#0b1020 55%,#050814 100%);
    }

    .slide::before{
      content:"";
      position:absolute;
      width:360px;
      height:360px;
      right:-120px;
      bottom:-150px;
      border-radius:50%;
      background:linear-gradient(135deg,rgba(110,168,255,.35),rgba(184,137,255,.18));
      filter:blur(5px);
    }

    .slide.active{
      display:flex;
      flex-direction:column;
      justify-content:center;
      animation:enter .45s ease both;
    }

    @keyframes enter{
      from{opacity:0;transform:translateY(14px) scale(.995)}
      to{opacity:1;transform:translateY(0) scale(1)}
    }

    .eyebrow{
      width:max-content;
      font-size:15px;
      font-weight:700;
      letter-spacing:.14em;
      text-transform:uppercase;
      color:#dbeafe;
      padding:10px 16px;
      margin-bottom:26px;
      border:1px solid var(--border);
      border-radius:999px;
      background:rgba(255,255,255,.06);
      backdrop-filter:blur(14px);
    }

    h1{
      max-width:1050px;
      font-size:clamp(52px,6.1vw,92px);
      line-height:.99;
      letter-spacing:-.055em;
      font-weight:850;
    }

    h2{
      font-size:clamp(42px,4.5vw,72px);
      line-height:1.02;
      letter-spacing:-.045em;
      font-weight:820;
      margin-bottom:20px;
    }

    .gradient-text{
      background:linear-gradient(90deg,var(--blue),#99a7ff,var(--purple),var(--pink));
      -webkit-background-clip:text;
      background-clip:text;
      color:transparent;
    }

    .lead{
      max-width:880px;
      margin-top:28px;
      color:var(--muted);
      font-size:clamp(20px,1.7vw,28px);
      line-height:1.55;
    }

    .meta{
      display:flex;
      gap:12px;
      flex-wrap:wrap;
      margin-top:34px;
    }

    .pill{
      padding:11px 15px;
      border-radius:14px;
      font-size:15px;
      color:#d9e3f4;
      background:rgba(255,255,255,.06);
      border:1px solid var(--border);
    }

    .grid3{
      display:grid;
      grid-template-columns:repeat(3,1fr);
      gap:22px;
      margin-top:34px;
    }

    .card{
      min-height:230px;
      padding:28px;
      border-radius:26px;
      background:var(--card);
      border:1px solid var(--border);
      box-shadow:0 22px 70px rgba(0,0,0,.20);
      backdrop-filter:blur(18px);
    }

    .icon{
      width:52px;height:52px;
      display:grid;place-items:center;
      margin-bottom:28px;
      border-radius:17px;
      font-size:22px;
      font-weight:800;
      background:linear-gradient(135deg,rgba(110,168,255,.9),rgba(184,137,255,.9));
      box-shadow:0 16px 42px rgba(91,110,255,.25);
    }

    .card h3{
      font-size:25px;
      margin-bottom:11px;
      letter-spacing:-.02em;
    }

    .card p{
      color:var(--muted);
      font-size:18px;
      line-height:1.5;
    }

    .split{
      display:grid;
      grid-template-columns:1.04fr .96fr;
      gap:5vw;
      align-items:center;
    }

    .visual{
      position:relative;
      height:min(58vh,560px);
      min-height:390px;
      border-radius:34px;
      border:1px solid rgba(255,255,255,.18);
      background:
        linear-gradient(145deg,rgba(110,168,255,.92),rgba(105,78,205,.86) 45%,rgba(255,133,183,.72));
      box-shadow:0 36px 100px rgba(0,0,0,.34);
      overflow:hidden;
    }

    .visual .orb{
      position:absolute;
      border-radius:50%;
      filter:blur(2px);
      background:rgba(255,255,255,.22);
    }

    .visual .orb.one{width:200px;height:200px;right:-55px;top:-30px}
    .visual .orb.two{width:150px;height:150px;left:-35px;bottom:-45px}

    .glass-window{
      position:absolute;
      inset:8%;
      border-radius:27px;
      border:1px solid rgba(255,255,255,.28);
      background:rgba(10,18,35,.24);
      backdrop-filter:blur(14px);
      padding:26px;
      display:flex;
      flex-direction:column;
      gap:16px;
    }

    .fake-line{
      height:14px;
      border-radius:999px;
      background:rgba(255,255,255,.80);
    }
    .fake-line.small{width:40%}
    .fake-line.mid{width:72%;opacity:.55}
    .fake-line.long{width:92%;opacity:.38}
    .fake-chart{
      margin-top:auto;
      height:48%;
      display:flex;
      align-items:flex-end;
      gap:12px;
      padding-top:20px;
    }
    .barx{
      flex:1;
      border-radius:10px 10px 4px 4px;
      background:rgba(255,255,255,.75);
    }

    .steps{
      margin-top:30px;
      display:flex;
      flex-direction:column;
      gap:16px;
      max-width:880px;
    }

    .step{
      display:grid;
      grid-template-columns:58px 1fr;
      gap:18px;
      align-items:center;
      padding:20px 22px;
      border-radius:22px;
      background:rgba(255,255,255,.07);
      border:1px solid var(--border);
    }

    .step .n{
      width:48px;height:48px;
      display:grid;place-items:center;
      border-radius:16px;
      background:linear-gradient(135deg,var(--blue),var(--purple));
      color:#07111e;
      font-size:20px;
      font-weight:900;
    }

    .step h3{font-size:23px;margin-bottom:5px}
    .step p{font-size:17px;color:var(--muted);line-height:1.45}

    .big-number{
      font-size:clamp(100px,14vw,210px);
      font-weight:900;
      letter-spacing:-.08em;
      line-height:.8;
      background:linear-gradient(110deg,#fff,#b7ceff 45%,#c395ff);
      -webkit-background-clip:text;
      background-clip:text;
      color:transparent;
    }

    .result-row{
      display:grid;
      grid-template-columns:1fr 1fr;
      gap:26px;
      margin-top:34px;
      max-width:900px;
    }

    .stat{
      padding:24px;
      border-radius:22px;
      border:1px solid var(--border);
      background:rgba(255,255,255,.07);
    }
    .stat strong{font-size:32px}
    .stat span{display:block;color:var(--muted);margin-top:8px;font-size:16px}

    .footer{
      position:absolute;
      left:6vw;
      right:6vw;
      bottom:3.8vh;
      display:flex;
      justify-content:space-between;
      align-items:center;
      color:#7f8ba1;
      font-size:14px;
    }

    .progress{
      width:220px;height:6px;
      border-radius:999px;
      background:rgba(255,255,255,.10);
      overflow:hidden;
    }
    .progress > div{
      height:100%;
      width:20%;
      border-radius:inherit;
      background:linear-gradient(90deg,var(--blue),var(--purple),var(--pink));
      transition:width .3s ease;
    }

    .nav{
      position:fixed;
      z-index:999;
      right:24px;
      bottom:20px;
      display:flex;
      gap:10px;
    }

    button{
      width:48px;height:48px;
      border:1px solid rgba(255,255,255,.12);
      border-radius:16px;
      color:#fff;
      background:rgba(15,23,42,.58);
      backdrop-filter:blur(14px);
      font-size:22px;
      cursor:pointer;
      transition:.2s;
    }
    button:hover{transform:translateY(-2px);background:rgba(255,255,255,.14)}

    @media (max-width:900px){
      .slide{padding:7vh 7vw}
      .grid3{grid-template-columns:1fr}
      .card{min-height:auto}
      .split{grid-template-columns:1fr}
      .visual{height:34vh;min-height:260px}
      .footer{left:7vw;right:7vw}
      .result-row{grid-template-columns:1fr}
    }

    @media print{
      body{overflow:visible;background:white}
      .deck{height:auto}
      .slide{
        position:relative;
        display:flex !important;
        width:13.333in;
        height:7.5in;
        page-break-after:always;
        -webkit-print-color-adjust:exact;
        print-color-adjust:exact;
      }
      .nav{display:none}
    }
  </style>
</head>
<body>
  <main class="deck">
    <section class="slide active">
      <div class="eyebrow">AI Presentation • 2026</div>
      <h1>Tạo slide <span class="gradient-text">đẹp, hiện đại</span><br/>bằng HTML</h1>
      <p class="lead">
        Một template 16:9 tối giản, phù hợp cho báo cáo dự án AI, seminar,
        đồ án hoặc demo sản phẩm.
      </p>
      <div class="meta">
        <div class="pill">16:9 Layout</div>
        <div class="pill">Keyboard Navigation</div>
        <div class="pill">Print to PDF</div>
      </div>
      <div class="footer">
        <span>Modern HTML Deck</span>
        <div class="progress"><div></div></div>
        <span>01 / 05</span>
      </div>
    </section>

    <section class="slide">
      <div class="split">
        <div>
          <div class="eyebrow">01 • Tổng quan</div>
          <h2>Biến nội dung phức tạp thành <span class="gradient-text">câu chuyện rõ ràng</span></h2>
          <p class="lead">
            Mỗi slide chỉ giữ lại một thông điệp chính. Khoảng trắng lớn,
            typography mạnh và visual đơn giản giúp người xem tập trung.
          </p>
        </div>
        <div class="visual">
          <div class="orb one"></div>
          <div class="orb two"></div>
          <div class="glass-window">
            <div class="fake-line small"></div>
            <div class="fake-line mid"></div>
            <div class="fake-line long"></div>
            <div class="fake-chart">
              <div class="barx" style="height:35%"></div>
              <div class="barx" style="height:52%"></div>
              <div class="barx" style="height:45%"></div>
              <div class="barx" style="height:72%"></div>
              <div class="barx" style="height:63%"></div>
              <div class="barx" style="height:88%"></div>
            </div>
          </div>
        </div>
      </div>
      <div class="footer">
        <span>Modern HTML Deck</span>
        <div class="progress"><div></div></div>
        <span>02 / 05</span>
      </div>
    </section>

    <section class="slide">
      <div class="eyebrow">02 • Điểm nổi bật</div>
      <h2>Thiết kế tập trung vào <span class="gradient-text">trải nghiệm trình bày</span></h2>
      <div class="grid3">
        <article class="card">
          <div class="icon">A</div>
          <h3>Typography mạnh</h3>
          <p>Tiêu đề lớn, khoảng cách hợp lý và phân cấp thông tin rõ ràng.</p>
        </article>
        <article class="card">
          <div class="icon">B</div>
          <h3>Visual hiện đại</h3>
          <p>Gradient, glassmorphism và card layout tạo cảm giác cao cấp.</p>
        </article>
        <article class="card">
          <div class="icon">C</div>
          <h3>Dễ chỉnh sửa</h3>
          <p>Chỉ cần sửa text trong từng section, không cần framework.</p>
        </article>
      </div>
      <div class="footer">
        <span>Modern HTML Deck</span>
        <div class="progress"><div></div></div>
        <span>03 / 05</span>
      </div>
    </section>

    <section class="slide">
      <div class="eyebrow">03 • Workflow</div>
      <h2>Quy trình trình bày <span class="gradient-text">4 bước</span></h2>
      <div class="steps">
        <div class="step">
          <div class="n">1</div>
          <div><h3>Problem</h3><p>Nêu bối cảnh, pain point và lý do cần giải quyết.</p></div>
        </div>
        <div class="step">
          <div class="n">2</div>
          <div><h3>Solution</h3><p>Giới thiệu cách tiếp cận, kiến trúc hoặc mô hình.</p></div>
        </div>
        <div class="step">
          <div class="n">3</div>
          <div><h3>Implementation</h3><p>Mô tả pipeline, công nghệ và cách triển khai.</p></div>
        </div>
        <div class="step">
          <div class="n">4</div>
          <div><h3>Result</h3><p>Đưa ra số liệu, hình ảnh minh họa và kết luận.</p></div>
        </div>
      </div>
      <div class="footer">
        <span>Modern HTML Deck</span>
        <div class="progress"><div></div></div>
        <span>04 / 05</span>
      </div>
    </section>

    <section class="slide">
      <div class="eyebrow">04 • Kết quả</div>
      <div class="big-number">100%</div>
      <h2 style="margin-top:28px">HTML thuần, <span class="gradient-text">không cần thư viện</span></h2>
      <p class="lead">
        Mở trực tiếp bằng Chrome, dùng phím mũi tên để chuyển slide
        và có thể Print → Save as PDF khi cần nộp báo cáo.
      </p>
      <div class="result-row">
        <div class="stat"><strong>← →</strong><span>Điều hướng bằng bàn phím</span></div>
        <div class="stat"><strong>PDF</strong><span>In trực tiếp theo tỷ lệ 16:9</span></div>
      </div>
      <div class="footer">
        <span>Thank you</span>
        <div class="progress"><div></div></div>
        <span>05 / 05</span>
      </div>
    </section>
  </main>

  <div class="nav">
    <button aria-label="Previous slide" onclick="prevSlide()">‹</button>
    <button aria-label="Next slide" onclick="nextSlide()">›</button>
  </div>

  <script>
    const slides = [...document.querySelectorAll('.slide')];
    const bars = [...document.querySelectorAll('.progress > div')];
    let current = 0;

    function render(index){
      slides[current].classList.remove('active');
      current = (index + slides.length) % slides.length;
      slides[current].classList.add('active');
      const pct = ((current + 1) / slides.length) * 100;
      bars.forEach(b => b.style.width = pct + '%');
    }

    function nextSlide(){ render(current + 1); }
    function prevSlide(){ render(current - 1); }

    document.addEventListener('keydown', e => {
      if (['ArrowRight','PageDown',' '].includes(e.key)) nextSlide();
      if (['ArrowLeft','PageUp'].includes(e.key)) prevSlide();
      if (e.key === 'Home') render(0);
      if (e.key === 'End') render(slides.length - 1);
    });

    bars.forEach(b => b.style.width = '20%');
  </script>
</body>
</html>
"""

path = Path("/output/slide_html_dep.html")
path.write_text(html, encoding="utf-8")
print(f"Đã tạo file: {path}")

