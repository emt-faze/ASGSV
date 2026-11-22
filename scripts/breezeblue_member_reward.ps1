# Windows UI Automation, scoped to one new official BreezeBlue window.
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$AE = [Windows.Automation.AutomationElement]
$TS = [Windows.Automation.TreeScope]
$CT = [Windows.Automation.ControlType]
$Any = [Windows.Automation.Condition]::TrueCondition
$RewardEntry = '^(领取每日积分|Claim daily credits)$'
$Redeem = '^(立即领取|领取\s*400\s*积分|Claim now|Claim 400 credits)$'
$Confirm = '^(确认领取|确定领取|Confirm claim|Confirm redemption)$'
$PaidPattern = '^(?:[+＋]?\s*\d[\d,.]*\s*积分已到账|今日已领取|本轮已领取|领取成功|已领取今日奖励|Claimed today|Already claimed|Successfully claimed)(?:[。！!]|$)'
$Challenge = '验证您是(?:人类|真人)|人机验证|verify (?:that )?you are human|human verification'
$Rejected = '^(领取失败|签到失败|无法领取|操作过于频繁|请求过于频繁|网络异常|网络错误|failed to claim|claim failed|too many requests)'
function Event-Json($value) {
    [Console]::Out.WriteLine('ASGSV-GIFT ' + ($value | ConvertTo-Json -Compress -Depth 3))
    [Console]::Out.Flush()
}
function Prop($property,$value) {
    return [Windows.Automation.PropertyCondition]::new($property,$value)
}
function Descendants($element,$type) {
    return $element.FindAll($TS::Descendants,(Prop $AE::ControlTypeProperty $type))
}
function BrowserWindows {
    return $AE::RootElement.FindAll($TS::Children,(Prop $AE::ClassNameProperty 'Chrome_WidgetWin_1'))
}
function SameAddress($win,$url) {
    $edits = $win.FindAll($TS::Descendants,(Prop $AE::ClassNameProperty 'OmniboxViewViews'))
    if ($edits.Count -ne 1) { return $false }
    $value = $null
    if (-not $edits[0].TryGetCurrentPattern([Windows.Automation.ValuePattern]::Pattern,[ref]$value)) { return $false }
    # Chromium elides https:// in an unfocused omnibox. Reject all other
    # protocols, ports, paths and fragments, including explicit http://.
    $address = $value.Current.Value
    return $address -ceq $url -or $address -ceq $url.Substring(8)
}
function OwnedDocument($win,$url) {
    if (-not (SameAddress $win $url)) { throw '官网窗口的地址已改变，领取已停止。' }
    $docs = @(Descendants $win $CT::Document)
    if ($docs.Count -eq 1) { return $docs[0] }
}
function NamedButtons($root,$pattern) {
    foreach ($e in (Descendants $root $CT::Button)) {
        if ($e.Current.Name -match $pattern -and -not $e.Current.IsOffscreen) { $e }
    }
}
function InvokeButton($e) {
    $invoke = $null
    $label = $e.Current.Name
    if (-not $e.Current.IsEnabled) { throw ('官网按钮当前不可用：' + $label) }
    if ($e.TryGetCurrentPattern([Windows.Automation.InvokePattern]::Pattern,[ref]$invoke)) {
        $invoke.Invoke()
        return
    }
    $expand = $null
    if ($label -match '^(修改资料|Edit profile)$' -and $e.TryGetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern,[ref]$expand) -and
        $expand.Current.ExpandCollapseState -eq [Windows.Automation.ExpandCollapseState]::Collapsed) {
        $expand.Expand()
        return
    }
    throw ('官网按钮未提供可执行操作：' + $label)
}
function NewMemberWindow($exe,$url) {
    $old = @(BrowserWindows | ForEach-Object { $_.Current.NativeWindowHandle })
    $info = [Diagnostics.ProcessStartInfo]::new($exe,('--new-window ' + $url))
    $info.UseShellExecute = $true
    $process = [Diagnostics.Process]::Start($info)
    if ($process) { $process.Dispose() }
    $until = [datetime]::UtcNow.AddSeconds(22)
    do {
        foreach ($win in (BrowserWindows)) {
            if ($win.Current.NativeWindowHandle -in $old) { continue }
            try {
                $owner = [Diagnostics.Process]::GetProcessById($win.Current.ProcessId)
                if ($owner.MainModule.FileName -ieq $exe -and (SameAddress $win $url)) { return $win }
            } catch [Windows.Automation.ElementNotAvailableException] { continue }
        }
        Start-Sleep -Milliseconds 250
    } while ([datetime]::UtcNow -lt $until)
    throw '没有找到本次打开的官网窗口。请先在所选浏览器登录配音账号，再检测自动领取。'
}
function CloseMemberWindow($win,$url) {
    if (-not $win) { return }
    try {
        if (-not (SameAddress $win $url)) { return }
        $tabs = @(Descendants $win $CT::TabItem)
        if ($tabs.Count -ne 1) { return }
        $view = $null
        if ($win.TryGetCurrentPattern([Windows.Automation.WindowPattern]::Pattern,[ref]$view)) { $view.Close() }
    } catch [Windows.Automation.ElementNotAvailableException] { return } # user already closed it
}
function CheckPage($doc) {
    foreach ($e in (Descendants $doc $CT::Text)) {
        $label = $e.Current.Name
        if ($label -match $Challenge -and -not $e.Current.IsOffscreen) { throw '官网要求本人验证，请先在浏览器完成。' }
        if ($label -match $Rejected -and -not $e.Current.IsOffscreen) { throw ('官网提示：' + $Matches[1]) }
    }
}
function MatchAccount($win,$url,$prefix) {
    $until = [datetime]::UtcNow.AddSeconds(32)
    $listSince = $null
    do {
        $doc = OwnedDocument $win $url
        if ($doc) {
            CheckPage $doc
            $welcome = @(NamedButtons $doc '^(关闭每日奖励|Close daily reward)$')
            if ($welcome.Count -eq 1) { InvokeButton $welcome[0]; Start-Sleep -Milliseconds 250; continue }
            # Only the public masked identifier in the official key table.
            $pattern = '(?<![A-Za-z0-9_-])' + [regex]::Escape($prefix) + '(?![A-Za-z0-9_-])'
            foreach ($table in (Descendants $doc $CT::Table)) {
                $cells = @(Descendants $table $CT::DataItem)
                $matching = @($cells | Where-Object { $_.Current.Name -cmatch $pattern })
                if ($matching.Count -eq 1) { return }
                if ($matching.Count -gt 1) { throw '官网密钥列表中出现多个相同标识，已停止领取。' }
                if ($cells.Count -gt 0 -and -not $listSince) { $listSince = [datetime]::UtcNow }
            }
        }
        if ($listSince -and ([datetime]::UtcNow - $listSince).TotalSeconds -gt 4) { break }
        Start-Sleep -Milliseconds 350
    } while ([datetime]::UtcNow -lt $until)
    throw '该浏览器的官网密钥列表未能匹配当前配音密钥。请在浏览器确认登录账号后重试。'
}
function GiftMenu($doc) {
    foreach ($menu in (Descendants $doc $CT::Menu)) {
        $entry = @(NamedButtons $menu $RewardEntry)
        if ($entry.Count -eq 1) { return $menu }
    }
}
function ReadGift($menu) {
    $section = $false
    $parts = [Collections.Generic.List[string]]::new()
    $button = $null
    $buttonName = ''
    $paid = $false
    $reset = $false
    foreach ($e in $menu.FindAll($TS::Descendants,$Any)) {
        $n = ([string]$e.Current.Name).Trim()
        $type = $e.Current.ControlType
        if ($type -eq $CT::Button -and $n -match $RewardEntry) { $section = $true; continue }
        if (-not $section -or $e.Current.IsOffscreen -or -not $n) { continue }
        if ($n -match '^(语言|主题|修改资料|Language|Theme|Edit profile)$') { break }
        if ($n -match $Challenge) { throw '官网要求本人验证，请先在浏览器完成。' }
        if ($n -match $Rejected) { throw ('官网提示：' + $Matches[1]) }
        if ($n -match $PaidPattern) { $paid = $true }
        if ($n -match '后可领取下一轮|下次领取倒计时|next claim in') { $reset = $true }
        if ($type -eq $CT::Text -and $n.Length -le 150) { $parts.Add($n) }
        if ($type -eq $CT::Button -and ($n -match $Redeem -or $n -match $Confirm) -and $e.Current.IsEnabled) {
            if ($button) { throw '官网同时显示多个领取按钮，未执行领取。' }
            $button = $e
            $buttonName = $n
        }
    }
    if ($parts.Count -gt 40) { throw '官网奖励卡结构已改变，请更新工作台。' }
    return @{ button=$button; label=$buttonName; parts=$parts.ToArray(); paid=$paid; reset=$reset }
}
function VisitReward($win,$url,$prefix,$mode) {
    MatchAccount $win $url $prefix
    $until = [datetime]::UtcNow.AddSeconds(18)
    $opened = $false
    $expanded = $false
    $sent = $false
    $confirmed = $false
    $sawReset = $false
    do {
        $doc = OwnedDocument $win $url
        if (-not $doc) { Start-Sleep -Milliseconds 250; continue }
        CheckPage $doc
        $menu = GiftMenu $doc
        if (-not $menu -and -not $opened -and -not $sent) {
            $profile = @(NamedButtons $doc '^(修改资料|Edit profile)$')
            if ($profile.Count -eq 1) { InvokeButton $profile[0]; $opened=$true }
        }
        if ($menu) {
            $gift = ReadGift $menu
            if ($gift.paid) { if ($sent) {return 'acknowledged'}; return 'already' }
            $sawReset = $gift.reset
            if ($gift.button -and (-not $sent -or (-not $confirmed -and $gift.label -match $Confirm))) {
                $phase = if ($sent) {'confirm'} else {'offer'}
                Event-Json @{event=$phase; labels=$gift.parts; button=$gift.label}
                $decision = [Console]::In.ReadLine()
                if ($decision -ceq 'inspect' -and $mode -eq 'inspect') { return 'available' }
                if ($decision -cne 'redeem' -or $mode -ne 'claim') { return 'declined' }
                $doc = OwnedDocument $win $url
                if (-not $doc) { throw '领取前官网页面已改变，已停止。' }
                CheckPage $doc
                $same = GiftMenu $doc
                if (-not $same) { throw '领取前奖励卡已关闭。' }
                $now = ReadGift $same
                if ($now.paid) { if ($sent) {return 'acknowledged'}; return 'already' }
                if (-not $now.button -or $now.label -cne $gift.label -or
                    [string]::Join('|',$now.parts) -cne [string]::Join('|',$gift.parts)) {
                    throw '领取前奖励卡金额或按钮发生变化，请稍后复核。'
                }
                InvokeButton $now.button
                $confirmed = $sent -or $now.label -match $Confirm
                $sent = $true
                $until = [datetime]::UtcNow.AddSeconds(10)
            } elseif (-not $sent -and -not $expanded -and -not $sawReset) {
                $entry = @(NamedButtons $menu $RewardEntry)
                $expand = $null
                if ($entry.Count -eq 1 -and $entry[0].TryGetCurrentPattern([Windows.Automation.ExpandCollapsePattern]::Pattern,[ref]$expand) -and
                    $expand.Current.ExpandCollapseState -eq [Windows.Automation.ExpandCollapseState]::Collapsed) {
                    $expand.Expand()
                    $expanded=$true
                }
            }
        }
        Start-Sleep -Milliseconds 300
    } while ([datetime]::UtcNow -lt $until)
    if ($sent) { return 'unconfirmed' }
    if ($sawReset) { return 'unavailable' }
    throw '官网没有显示本账号的每日奖励卡，已停止领取。'
}
function RunMemberWindow {
    $win = $null
    $url = 'https://breezeblue.ai/zh/app/developer/api-keys#asgsv-reward-' + [guid]::NewGuid().ToString('N')
    try {
        $mode = $env:ASGSV_REWARD_MODE
        $prefix = $env:ASGSV_REWARD_PUBLIC_PREFIX
        $exe = $env:ASGSV_REWARD_BROWSER
        if ($mode -notin 'inspect','claim' -or $prefix -cnotmatch '^[A-Za-z0-9_-]{8,32}$' -or
            [IO.Path]::GetFileName($exe) -notin 'chrome.exe','msedge.exe') { throw '领取通道参数无效。' }
        $win = NewMemberWindow $exe $url
        $status = VisitReward $win $url $prefix $mode
        Event-Json @{event='result'; status=$status}
    } catch {
        Event-Json @{event='error'; message=$_.Exception.Message}
    } finally {
        CloseMemberWindow $win $url
    }
}
if ($MyInvocation.InvocationName -ne '.') { RunMemberWindow }
