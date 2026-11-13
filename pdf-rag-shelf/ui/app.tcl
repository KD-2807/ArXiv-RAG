package require Tk 8.6
set variant [lindex $argv 0]
set engine [lrange $argv 1 end]
if {$variant ne "python" || [llength $engine] == 0} {error "Use a supplied launcher."}
set cfgdir [file join $env(HOME) .config pdf-rag-shelf-$variant]
if {[info exists env(XDG_CONFIG_HOME)]} {set cfgdir [file join $env(XDG_CONFIG_HOME) pdf-rag-shelf-$variant]}
file mkdir $cfgdir
file attributes $cfgdir -permissions 0700
set cfgfile [file join $cfgdir settings.tcldata]
array set cfg [list root [file join $env(HOME) Downloads ArxivShelf] db [file join $env(HOME) .local share pdf-rag-shelf-$variant index-v3.sqlite3] model nomic-embed-text-v2-moe:latest chunk 600 overlap 80 batch 16 retry 0 relocate 0 query {} k 8 subject {}]
if {[file exists $cfgfile]} {
    file attributes $cfgfile -permissions 0600
    set f [open $cfgfile r]; set saved [read $f]; close $f
    if {![catch {dict size $saved}]} {
        foreach {key value} $saved {if {[info exists cfg($key)]} {set cfg($key) $value}}
    }
}
set channel ""
set status "Ready"
set cancelled 0
set killTimer ""
set generation 0
set closing 0
set controls {}
set prunePlan {}
set applyToken ""
array set hits {}
proc b64 {value} {return [binary encode base64 -maxlen 0 [encoding convertto utf-8 $value]]}
proc logMessage {message} {
    .main.log configure -state normal
    .main.log insert end "$message\n"
    if {[lindex [split [.main.log index end] .] 0] > 200} {.main.log delete 1.0 40.0}
    .main.log see end
    .main.log configure -state disabled
}
proc saveSettings {} {
    global cfg cfgfile
    set f [open $cfgfile.tmp w 0600]; file attributes $cfgfile.tmp -permissions 0600; fconfigure $f -encoding utf-8
    puts $f [array get cfg]; close $f
    file rename -force $cfgfile.tmp $cfgfile
    file attributes $cfgfile -permissions 0600
}
proc browse {kind} {
    global cfg
    if {$kind eq "root"} {set value [tk_chooseDirectory -title "Choose PDF folder"]} else {set value [tk_getSaveFile -title "Choose index database" -defaultextension .sqlite3]}
    if {$value ne ""} {set cfg($kind) $value}
}
proc busy {value} {
    global controls
    foreach widget $controls {$widget configure -state [expr {$value ? "disabled" : "normal"}]}
    .main.buttons.stop configure -state [expr {$value ? "normal" : "disabled"}]
    if {$value} {.main.progress start 15} else {.main.progress stop}
}
proc startJob {action} {
    global cfg engine channel status cancelled hits generation prunePlan applyToken
    if {$channel ne ""} {return}
    foreach field {chunk overlap k batch} {
        if {![string is integer -strict $cfg($field)]} {tk_messageBox -icon error -message "Enter a whole number for $field."; return}
    }
    if {$cfg(chunk) < 100 || $cfg(chunk) > 6000 || $cfg(overlap) < 0 || $cfg(overlap) >= $cfg(chunk) / 2 || $cfg(k) < 1 || $cfg(k) > 100 || $cfg(batch) < 1 || $cfg(batch) > 64} {
        tk_messageBox -icon error -message "Chunk: 100–6000. Overlap: 0 to less than half the chunk. Results: 1–100. Batch: 1–64."
        return
    }
    if {$cfg(root) eq "" || $cfg(db) eq "" || $cfg(model) eq ""} {tk_messageBox -icon error -message "Choose a PDF folder, database and model."; return}
    if {[catch {saveSettings} error]} {tk_messageBox -icon error -message $error; return}
    set prunePlan {}
    set backendAction [expr {$action eq "prune_apply" ? "prune" : $action}]
    set args [list $backendAction --db [b64 $cfg(db)] --ui --encoded]
    if {$action eq "index"} {
        lappend args --root [b64 $cfg(root)] --model [b64 $cfg(model)] --chunk $cfg(chunk) --overlap $cfg(overlap) --batch-size $cfg(batch)
        if {$cfg(retry)} {lappend args --retry-failed}
        if {$cfg(relocate)} {lappend args --relocate-root}
    } elseif {$action eq "search"} {
        lappend args --query [b64 $cfg(query)] --k $cfg(k) --subject [b64 $cfg(subject)]
        .main.results delete [.main.results children {}]
        array unset hits
        .main.preview configure -state normal; .main.preview delete 1.0 end; .main.preview configure -state disabled
    }
    if {$action eq "open-pdf"} {
        set selection [.main.results selection]
        if {$selection eq "" || ![info exists hits($selection)]} {return}
        lassign $hits($selection) path page
        lappend args --pdf-path [b64 $path] --page $page
    }
    if {$action eq "prune"} {lappend args --dry-run}
    if {$action eq "prune_apply"} {lappend args --apply-prune $applyToken}
    set command [list | setsid {*}$engine {*}$args 2>@1]
    if {[catch {open $command r} handle]} {tk_messageBox -icon error -message $handle; return}
    set channel $handle
    fconfigure $channel -blocking 0 -buffering line -encoding utf-8
    fileevent $channel readable receive
    set cancelled 0
    incr generation
    set status "Running $action…"
    busy 1
}
proc receive {} {
    global channel status cancelled closing hits killTimer prunePlan applyToken
    while {[gets $channel line] >= 0} {
        set parts [split $line "\t"]
        set event [lindex $parts 0]
        if {$event ni {log error hit prune}} {logMessage $line; continue}
        set values {}
        if {[catch {foreach encoded [lrange $parts 1 end] {lappend values [encoding convertfrom utf-8 [binary decode base64 $encoded]]}}]} {logMessage $line; continue}
        if {$event eq "prune"} {
            set prunePlan $values
        } elseif {$event eq "hit"} {
            addHit $values
        } else {
            set status [lindex $values 0]
            logMessage $status
        }
    }
    if {[eof $channel]} {
        if {$killTimer ne ""} {after cancel $killTimer; set killTimer ""}
        fileevent $channel readable {}
        fconfigure $channel -blocking 1
        set failed [catch {close $channel} detail]
        set channel ""
        busy 0
        if {$cancelled} {set status "Stopped. Completed PDFs remain indexed."} elseif {$failed} {set status "Finished with errors. See the log."} else {set status "Finished successfully."}
        logMessage $status
        if {$closing} {exit}
        if {!$failed && !$cancelled && [llength $prunePlan] == 6} {
            after idle confirmPrune
        }
    }
}
proc stopJob {} {
    global channel cancelled status killTimer generation
    if {$channel eq "" || $cancelled} {return}
    set cancelled 1
    set status "Stopping…"
    set processes [pid $channel]
    foreach process $processes {catch {exec kill -TERM -- -$process}}
    set killTimer [after 5000 [list forceStop $generation $processes]]
}
proc forceStop {job processes} {
    global channel generation status killTimer
    if {$channel eq "" || $generation != $job} {return}
    set killTimer ""
    set status "Forcing the worker to stop…"
    foreach process $processes {catch {exec kill -KILL -- -$process}}
}
proc pruneIndex {} {startJob prune}
proc confirmPrune {} {
    global prunePlan applyToken channel
    if {$channel ne "" || [llength $prunePlan] != 6} {return}
    lassign $prunePlan files documents chunks errors token root
    set prunePlan {}
    if {$files == 0 && $documents == 0 && $chunks == 0 && $errors == 0} {
        logMessage "Cleanup preview: nothing to remove."
        return
    }
    set message "Indexed root: $root\n\nCleanup preview: $files source records, $documents documents, $chunks vector chunks and $errors error records will be deleted. Ensure all PDF storage is mounted. This removes index data; PDFs remain unchanged. Apply this exact preview?"
    if {[tk_messageBox -type yesno -icon warning -message $message] eq "yes"} {
        set applyToken $token
        startJob prune_apply
    }
}
proc closeApp {} {
    global channel closing
    catch {saveSettings}
    if {$channel eq ""} {exit} else {set closing 1; stopJob}
}
proc addHit {values} {
    global hits
    if {[llength $values] < 5 || [llength $values] % 2 != 1} {return}
    lassign $values path page score text subject
    set aliases [list $path $subject {*}[lrange $values 5 end]]
    set paths {}
    foreach {alias category} $aliases {lappend paths $alias}
    foreach {alias category} $aliases {
        set id [.main.results insert {} end -values [list [file tail $alias] $page $score $category]]
        set hits($id) [list $alias $page $text $paths]
    }
}
proc showHit {} {
    global hits
    set selection [.main.results selection]
    if {$selection eq "" || ![info exists hits($selection)]} {return}
    lassign $hits($selection) path page text paths
    .main.preview configure -state normal
    .main.preview delete 1.0 end
    .main.preview insert end "$path\nPage $page\nAll sources:\n[join $paths \n]\n\n$text"
    .main.preview configure -state disabled
}
proc openPdf {} {startJob open-pdf}
wm title . "PDF RAG Shelf"
set width [expr {min(1000, [winfo screenwidth .] - 60)}]
set height [expr {min(870, [winfo screenheight .] - 100)}]
wm geometry . ${width}x${height}
wm minsize . 620 440
ttk::style theme use clam
ttk::style configure TFrame -background #f4f6fa
ttk::style configure TLabel -background #f4f6fa -foreground #1c2b43
ttk::style configure TButton -padding {10 6}
ttk::style configure Title.TLabel -font {Sans 22 bold}
ttk::frame .main -padding 22
grid columnconfigure . 0 -weight 1
grid rowconfigure . 0 -weight 1
grid .main -row 0 -column 0 -sticky nsew
grid columnconfigure .main 1 -weight 1
grid rowconfigure .main 11 -weight 1
grid rowconfigure .main 13 -weight 1
ttk::label .main.title -text "PDF RAG Shelf" -style Title.TLabel
grid .main.title -row 0 -columnspan 3 -sticky w -pady {0 6}
ttk::label .main.subtitle -wraplength 600 -text "Local papers. Searchable knowledge. Powered by your Ollama models."
grid .main.subtitle -row 1 -columnspan 3 -sticky w -pady {0 18}
foreach {field label row} {root "PDF folder" 2 db "Index database" 3} {
    ttk::label .main.${field}label -text $label
    ttk::entry .main.$field -textvariable cfg($field)
    ttk::button .main.${field}browse -text "Browse…" -command [list browse $field]
    grid .main.${field}label -row $row -column 0 -sticky w -padx {0 14}
    grid .main.$field -row $row -column 1 -sticky ew -pady 4
    grid .main.${field}browse -row $row -column 2 -padx {8 0}
    lappend controls .main.$field .main.${field}browse
}
ttk::label .main.modellabel -text "Embedding model"
ttk::combobox .main.model -textvariable cfg(model) -values {nomic-embed-text-v2-moe:latest dengcao/Qwen3-Embedding-8B:Q4_K_M}
grid .main.modellabel -row 4 -column 0 -sticky w
grid .main.model -row 4 -column 1 -columnspan 2 -sticky ew -pady 5
lappend controls .main.model
ttk::frame .main.options
grid .main.options -row 5 -column 1 -columnspan 2 -sticky w -pady 8
foreach {field label maximum} {chunk "Chunk characters" 6000 overlap "Overlap" 1000 batch "Batch" 64} {
    ttk::label .main.options.${field}label -text $label
    ttk::spinbox .main.options.$field -from 0 -to $maximum -width 7 -textvariable cfg($field)
    pack .main.options.${field}label .main.options.$field -side left -padx {0 10}
    lappend controls .main.options.$field
}
ttk::frame .main.buttons
grid .main.buttons -row 6 -columnspan 3 -sticky w -pady {0 12}
ttk::button .main.buttons.index -text "Index PDFs" -command {startJob index}
ttk::button .main.buttons.save -text "Save settings" -command saveSettings
ttk::checkbutton .main.buttons.retry -text "Retry failed PDFs" -variable cfg(retry)
ttk::checkbutton .main.buttons.relocate -text "Use new root" -variable cfg(relocate)
ttk::button .main.buttons.prune -text "Clean index" -command pruneIndex
ttk::button .main.buttons.stop -text "Stop" -command stopJob -state disabled
pack .main.buttons.index .main.buttons.save .main.buttons.stop -side left -padx {0 8}
pack .main.buttons.retry .main.buttons.relocate .main.buttons.prune -side left -padx {0 4}
lappend controls .main.buttons.index .main.buttons.save .main.buttons.retry .main.buttons.relocate .main.buttons.prune
ttk::label .main.querylabel -text "Search question"
ttk::entry .main.query -textvariable cfg(query)
ttk::button .main.search -text "Search" -command {startJob search}
grid .main.querylabel -row 7 -column 0 -sticky w
grid .main.query -row 7 -column 1 -sticky ew
grid .main.search -row 7 -column 2 -padx {8 0}
lappend controls .main.query .main.search
bind .main.query <Return> {startJob search}
ttk::frame .main.filters
grid .main.filters -row 8 -column 1 -columnspan 2 -sticky ew -pady 8
ttk::label .main.filters.sl -text "Subject (empty = all)"
ttk::entry .main.filters.subject -textvariable cfg(subject) -width 24
ttk::label .main.filters.kl -text "Results"
ttk::spinbox .main.filters.k -from 1 -to 100 -textvariable cfg(k) -width 5
pack .main.filters.sl .main.filters.subject .main.filters.kl .main.filters.k -side left -padx {0 10}
lappend controls .main.filters.subject .main.filters.k
ttk::progressbar .main.progress -mode indeterminate
grid .main.progress -row 9 -columnspan 3 -sticky ew -pady 6
ttk::label .main.status -textvariable status -wraplength 600
grid .main.status -row 10 -columnspan 3 -sticky w -pady 4
ttk::treeview .main.results -columns {file page score subject} -show headings -height 5 -selectmode browse
foreach {key label width} {file "PDF" 430 page "Page" 60 score "Similarity" 90 subject "Subject" 200} {
    .main.results heading $key -text $label
    .main.results column $key -width $width
}
grid .main.results -row 11 -columnspan 3 -sticky nsew
bind .main.results <<TreeviewSelect>> showHit
ttk::button .main.open -text "Open selected PDF at page" -command openPdf
grid .main.open -row 12 -columnspan 3 -sticky w -pady 6
text .main.preview -height 6 -wrap word -font {Sans 11} -background white -relief flat -padx 10 -pady 10 -state disabled
grid .main.preview -row 13 -columnspan 3 -sticky nsew
text .main.log -height 5 -wrap word -font {Monospace 9} -background #eaf0f7 -relief flat -padx 8 -pady 8 -state disabled
grid .main.log -row 14 -columnspan 3 -sticky ew -pady {10 0}
wm protocol . WM_DELETE_WINDOW closeApp
