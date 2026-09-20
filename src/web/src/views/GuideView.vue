<script setup lang="ts">
import SiteFooter from "@/components/SiteFooter.vue";
</script>

<template>
  <div data-page="guide">
    <header class="topbar">
      <RouterLink class="brand-button" to="/"><img class="brand-mark" src="/purslyx-logo.png" alt="Purslyx 品牌标志" /><span>Purslyx</span></RouterLink>
      <RouterLink class="guide-link active" to="/guide">使用说明</RouterLink>
      <span class="topbar-spacer" />
      <a class="button outline" href="/purslyx-job-capture.user.js">安装脚本</a>
      <RouterLink class="button soft" to="/app/login">登录</RouterLink>
      <RouterLink class="button primary" to="/app/register">创建账号</RouterLink>
    </header>

    <main class="public-main guide-main">
      <section class="guide-hero">
        <div>
          <div class="eyebrow">PURSLYX QUICK START</div>
          <h1>第一次使用，照着这 6 步走。</h1>
          <p>你不需要一开始就弄懂所有功能。先准备一份真实简历和一条岗位期望，再把岗位收进匹配池；需要投递时，系统会告诉你哪些证据匹配、哪里需要补充。</p>
          <div class="hero-actions"><RouterLink class="button primary" to="/app/register">现在开始</RouterLink><a class="button outline" href="#first-match">查看完整流程</a></div>
        </div>
        <div class="guide-quick-card">
          <div class="eyebrow">最短路径</div>
          <h2>资料 → 期望 → 岗位 → 匹配 → 报告</h2>
          <div class="guide-quick-list"><div><span>01</span><strong>确认简历</strong><small>只使用你确认过的事实</small></div><div><span>02</span><strong>保存期望</strong><small>可以保存多条，匹配时默认合并</small></div><div><span>03</span><strong>收集岗位</strong><small>插件只抓当前打开的完整 JD</small></div><div><span>04</span><strong>开始匹配</strong><small>选择简历后再消耗分析次数</small></div></div>
        </div>
      </section>

      <nav class="guide-anchor-bar" aria-label="使用说明目录">
        <a href="#first-match">首次匹配</a><a href="#capture">抓取岗位</a><a href="#match">匹配池</a><a href="#after-match">匹配后</a><a href="#recruiter">招聘方</a><a href="#faq">常见问题</a>
      </nav>

      <section class="guide-callout">
        <div class="guide-callout-icon">!</div>
        <div><strong>先记住 3 件事</strong><p>浏览器插件只负责把岗位写入你的私有匹配池，不会自动投递；岗位期望不会绑定某一个岗位，点击匹配时会默认把所有有效期望合并成一次请求；AI 不会替你编造项目、公司或数字，解析草稿和改写建议都要由你确认。</p></div>
      </section>

      <section id="first-match" class="guide-section">
        <div class="section-heading"><div><div class="eyebrow">FIRST MATCH</div><h2>首次匹配完整流程</h2></div><p>按顺序完成一次，之后抓取新岗位只需要重复第 5、6 步。</p></div>
        <div class="guide-step-grid">
          <article class="guide-step-card"><span class="guide-step-no">01</span><h3>注册并登录</h3><p>创建账号时选择“求职者”。注册身份决定工作台类型，后续不会在账号内切换。</p><RouterLink to="/app/register">创建求职账号 →</RouterLink></article>
          <article class="guide-step-card"><span class="guide-step-no">02</span><h3>新增并确认简历</h3><p>进入“简历”，点击“新增简历”，粘贴文本或上传 PDF、DOC、DOCX。解析完成后逐项检查，点击“确认这个版本”。</p><RouterLink to="/app/seeker/resume">去上传简历 →</RouterLink></article>
          <article class="guide-step-card"><span class="guide-step-no">03</span><h3>保存岗位期望</h3><p>在同一页新增一条或多条期望，填写岗位方向、城市、办公方式和薪资。未知与不限要明确选择，别用猜测代替。</p><RouterLink to="/app/seeker/resume">去设置期望 →</RouterLink></article>
          <article class="guide-step-card"><span class="guide-step-no">04</span><h3>安装岗位采集脚本</h3><p>浏览器安装 Tampermonkey，再点击官网“安装脚本”。脚本只读取当前岗位详情页，不读取招聘平台密码或 Cookie。</p><a href="#capture">看安装与抓取 →</a></article>
          <article class="guide-step-card"><span class="guide-step-no">05</span><h3>把 JD 收进匹配池</h3><p>打开 BOSS 直聘或猎聘的岗位详情页，等待正文完整显示，点击岗位旁的“同步 Purslyx”，成功后点“打开匹配池”。</p><a href="#capture">看抓取步骤 →</a></article>
          <article class="guide-step-card"><span class="guide-step-no">06</span><h3>选择简历并开始匹配</h3><p>匹配池勾选岗位，点击“匹配”。只有多份简历时才需要选择；岗位期望默认全部参与，然后确认本次分析。</p><RouterLink to="/app/seeker/pool">打开匹配池 →</RouterLink></article>
        </div>
      </section>

      <section id="capture" class="guide-section">
        <div class="section-heading"><div><div class="eyebrow">BROWSER ASSISTANT</div><h2>抓取岗位：只抓完整 JD，不自动投递</h2></div><p>抓取和分析是两个动作。你可以先连续收集多个岗位，之后再集中比较。</p></div>
        <div class="guide-flow"><div><span>岗位详情页</span><small>BOSS / 猎聘</small></div><i>→</i><div><span>同步 Purslyx</span><small>当前页面的按钮</small></div><i>→</i><div><span>匹配池</span><small>保存岗位和抓取时间</small></div><i>→</i><div><span>选择后匹配</span><small>由你确认简历</small></div></div>
        <div class="guide-two-column">
          <article class="guide-panel"><div class="guide-panel-title"><span class="guide-step-no">A</span><h3>第一次安装</h3></div><ol class="guide-list"><li>先安装 <a href="https://www.tampermonkey.net/" target="_blank" rel="noreferrer">Tampermonkey</a>。</li><li>点击本页顶部或首页的“安装脚本”，在扩展页面确认安装。</li><li>保持 Purslyx 已登录，再打开 BOSS 直聘或猎聘的岗位详情页。</li></ol><a class="button primary small" href="/purslyx-job-capture.user.js">安装 Purslyx 脚本</a></article>
          <article class="guide-panel"><div class="guide-panel-title"><span class="guide-step-no">B</span><h3>每个岗位的操作</h3></div><ol class="guide-list"><li>确认岗位正文已经展开；如果页面显示“查看完整内容”，先在原平台展开。</li><li>点击脚本面板里的“同步 Purslyx”，等待提示“已获取……并直接写入匹配池”。</li><li>点击“打开匹配池”查看岗位。这里不会消耗 AI 次数，之后再决定是否匹配。</li></ol><div class="callout attention">如果正文被折叠、要求登录或平台触发验证码，脚本会停止，先处理原页面后点击“重试识别”。</div></article>
        </div>
      </section>

      <section id="match" class="guide-section">
        <div class="section-heading"><div><div class="eyebrow">MATCH POOL</div><h2>匹配池：收集、筛选，再开始 AI 分析</h2></div><p>岗位列表默认按入池时间从新到旧排列，也可以按匹配分数从高到低排列。</p></div>
        <div class="guide-two-column guide-match-columns">
          <article class="guide-panel"><h3>开始匹配</h3><ol class="guide-list"><li>在岗位列表勾选一个或多个岗位，点击上方“匹配”。</li><li>只有一份已确认简历时会直接使用；有多份简历时，弹窗选择本次使用哪一份。</li><li>所有有效岗位期望默认合并参与同一个岗位的匹配，不需要逐条选择。</li><li>确认后等待状态变为“已有结果”，点击“详情”或“报告”查看结果。</li></ol></article>
          <article class="guide-panel"><h3>怎么看列表</h3><div class="guide-status-list"><div><span class="tag neutral">排队中</span><p>任务已提交，等待后台 Worker 领取。</p></div><div><span class="tag opportunity">匹配执行中</span><p>模型正在分析岗位要求和简历证据。</p></div><div><span class="tag success">已有结果</span><p>可以打开报告、改写简历或开始面试。</p></div><div><span class="tag attention">匹配失败，可重试</span><p>打开详情查看原因，修复后重新匹配。</p></div></div></article>
        </div>
        <div class="guide-feature-grid"><div><strong>搜索与筛选</strong><p>支持岗位名称、公司、薪资、入池时间、匹配分数、状态和来源平台。</p></div><div><strong>查看详情</strong><p>点击岗位行的“详情”，可以查看完整 JD、地点、经验、学历、薪资和分析历史。</p></div><div><strong>批量处理</strong><p>可以跨页选择岗位后批量匹配；每个岗位仍只发起一次分析请求。</p></div></div>
      </section>

      <section id="after-match" class="guide-section">
        <div class="section-heading"><div><div class="eyebrow">AFTER MATCH</div><h2>报告不是终点，按缺口继续行动</h2></div><p>Purslyx 把匹配结果连接到简历事实、岗位版简历和 STAR 面试练习。</p></div>
        <div class="guide-action-grid"><article><span>01</span><h3>打开匹配报告</h3><p>看能力分、岗位条件、已匹配证据和待核实项。分数是参考，不是录用承诺。</p></article><article><span>02</span><h3>事实与改写</h3><p>先补充或确认真实事实，再生成岗位相关表达；不支持把未经证实的项目直接写进简历。</p></article><article><span>03</span><h3>岗位版简历</h3><p>在确认改写结果后生成岗位版，调整模块和排版，再导出 PDF。</p></article><article><span>04</span><h3>STAR 面试</h3><p>围绕当前岗位缺口练习 Situation、Task、Action、Result，提交回答后获得针对性反馈。</p></article></div>
      </section>

      <section id="recruiter" class="guide-section">
        <div class="section-heading"><div><div class="eyebrow">RECRUITER</div><h2>招聘方怎么用</h2></div><p>招聘账号不使用求职者匹配池，流程是“候选人资料 + JD + 候选人明确期望”。</p></div>
        <div class="guide-recruiter"><div class="guide-recruiter-step"><strong>1. 导入资料</strong><span>在“候选人资料”分别导入候选人简历和岗位 JD，解析后逐项确认。</span></div><div class="guide-recruiter-step"><strong>2. 记录期望</strong><span>只有候选人明确提供的期望才能保存，不能从现居地、旧薪资或 JD 推断。</span></div><div class="guide-recruiter-step"><strong>3. 生成报告</strong><span>选择候选人、岗位和可选的候选人期望，确认消耗 1 次后生成单人分析。</span></div><RouterLink class="button outline small" to="/app/register">创建招聘账号</RouterLink></div>
      </section>

      <section id="faq" class="guide-section">
        <div class="section-heading"><div><div class="eyebrow">FAQ</div><h2>常见问题</h2></div><p>遇到问题先看这里，仍无法解决再到工作台提交反馈。</p></div>
        <div class="guide-faq">
          <details open><summary>岗位一直显示“排队中”或“匹配执行中”怎么办？</summary><p>页面会自动刷新状态。“排队中”表示任务还没有被 Worker 领取；“匹配执行中”表示模型正在运行。长时间没有变化时打开“任务中心”查看任务，或联系管理员检查 Worker 日志。</p></details>
          <details><summary>为什么抓取到的 JD 不完整？</summary><p>先在 BOSS 或猎聘原页面登录并展开完整岗位正文，等待页面加载稳定，再点击“重试识别”。脚本不会用猜测补齐被折叠或无法读取的内容。</p></details>
          <details><summary>有多条岗位期望，会不会对同一个岗位扣很多次？</summary><p>不会。对同一个岗位，所有有效岗位期望会合并为一次模型请求；批量选择多个岗位时，按岗位数量提交匹配。</p></details>
          <details><summary>为什么匹配时还要选择简历？</summary><p>一个账号可以维护多份简历，不同岗位可能使用不同版本。只有多份简历时才弹窗选择；岗位期望不需要你逐条选择，默认全部参与。</p></details>
          <details><summary>Purslyx 会自动帮我投递或联系招聘者吗？</summary><p>不会。点击“去投递”只记录你主动发起的入口并打开原岗位页面，是否投递、沟通和提交简历都由你在招聘平台确认。</p></details>
          <details><summary>AI 会不会把不存在的经历写进简历？</summary><p>产品以已确认事实为边界。解析、报告和改写都保留证据引用；发现事实不准确时，请先回到资料页面修正并确认新版本。</p></details>
        </div>
      </section>

      <section class="product-cta"><div><div class="eyebrow">READY WHEN YOU ARE</div><h2>先完成一次真实匹配。</h2><p>不用一次学会全部功能，照着本页流程走一遍就够了。</p></div><div class="hero-actions"><RouterLink class="button primary" to="/app/register">创建账号</RouterLink><RouterLink class="button outline" to="/app/login">登录工作台</RouterLink></div></section>
    </main>
    <SiteFooter />
  </div>
</template>
