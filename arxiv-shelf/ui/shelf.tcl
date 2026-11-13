package require Tk 8.6
wm title . "arXiv Shelf"
wm geometry . 900x820
wm minsize . 760 700
ttk::style theme use clam
ttk::style configure TFrame -background #f4f6fa
ttk::style configure TLabel -background #f4f6fa -foreground #1b2940
ttk::style configure TButton -padding {12 7}
ttk::style configure Title.TLabel -font {Sans 22 bold}
ttk::style configure Sub.TLabel -foreground #52627a
set root ""
set contact ""
set mode limited
set limit 100
set status "Ready"
set busy 0
set activeControls {}
set keywordsEnabled 0
proc transmit {action} {
    global root contact mode limit keywordsEnabled
    set fields [list $root [.body.subjects get 1.0 end-1c] $mode $limit $contact]
    if {$keywordsEnabled} {lappend fields [.body.keywords get 1.0 end-1c]}
    set output [list $action]
    foreach value $fields {
        lappend output [binary encode base64 -maxlen 0 [encoding convertto utf-8 $value]]
    }
    puts stdout [join $output "\t"]
    flush stdout
}
proc chooseRoot {} {
    global root
    set chosen [tk_chooseDirectory -title "Choose download directory"]
    if {$chosen ne ""} {set root $chosen}
}
proc appendLog {message} {
    .body.log configure -state normal
    .body.log insert end "$message\n"
    if {[lindex [split [.body.log index end] .] 0] > 250} {
        .body.log delete 1.0 50.0
    }
    .body.log see end
    .body.log configure -state disabled
}
proc setBusy {value} {
    global busy activeControls
    set busy $value
    foreach widget $activeControls {
        if {$value} {$widget configure -state disabled} else {$widget configure -state normal}
    }
    if {$value} {
        .body.actions.stop configure -state normal
        .body.progress start 15
    } else {
        .body.actions.stop configure -state disabled
        .body.progress stop
    }
}
proc receive {} {
    global root contact mode limit status keywordsEnabled
    if {[eof stdin]} {exit}
    while {[gets stdin line] >= 0} {
        set parts [split $line "\t"]
        set action [lindex $parts 0]
        set values {}
        foreach encoded [lrange $parts 1 end] {
            lappend values [encoding convertfrom utf-8 [binary decode base64 $encoded]]
        }
        switch -- $action {
            init {
                lassign $values root subjects mode limit contact variant keywords
                .body.subjects delete 1.0 end
                .body.subjects insert 1.0 $subjects
                if {[llength $values] > 6} {enableKeywords $keywords}
                wm title . "arXiv Shelf · $variant"
                .body.title configure -text "arXiv Shelf / $variant"
            }
            log {set status [lindex $values 0]; appendLog $status}
            error {set status [lindex $values 0]; appendLog "ERROR: $status"; tk_messageBox -icon error -message $status}
            busy {setBusy [lindex $values 0]}
        }
    }
}
proc enableKeywords {keywords} {
    global keywordsEnabled
    set keywordsEnabled 1
    .body.keywords delete 1.0 end
    .body.keywords insert 1.0 $keywords
    foreach widget {.body.keywordlabel .body.keywords .body.keywordhint} {grid $widget}
    .body.modes.limited configure -text "New papers per subject or keyword"
    .body.modes.all configure -text "All in selected subjects and keywords"
}
proc closeWindow {} {
    global busy status
    if {$busy} {
        set status "Stopping safely before closing…"
        transmit quit
    } else {
        transmit quit
        exit
    }
}
ttk::frame .body -padding 24
pack .body -fill both -expand 1
grid columnconfigure .body 1 -weight 1
grid rowconfigure .body 14 -weight 1
ttk::label .body.title -text "arXiv Shelf" -style Title.TLabel
grid .body.title -row 0 -column 0 -columnspan 3 -sticky w -pady {0 4}
ttk::label .body.subtitle -text "Your papers, organised. A private desktop library." -style Sub.TLabel
grid .body.subtitle -row 1 -column 0 -columnspan 3 -sticky w -pady {0 20}
ttk::label .body.pathlabel -text "Download folder"
ttk::entry .body.path -textvariable root
ttk::button .body.browse -text "Browse…" -command chooseRoot
grid .body.pathlabel -row 2 -column 0 -sticky w -padx {0 12}
grid .body.path -row 2 -column 1 -sticky ew
grid .body.browse -row 2 -column 2 -padx {8 0}
ttk::label .body.subjectlabel -text "Subjects → folders"
grid .body.subjectlabel -row 3 -column 0 -sticky nw -pady {16 0}
text .body.subjects -height 5 -width 50 -font {Monospace 11} -background white -foreground #1b2940 -relief solid -borderwidth 1 -padx 10 -pady 8 -undo 1
grid .body.subjects -row 3 -column 1 -columnspan 2 -sticky ew -pady {16 4}
ttk::label .body.hint -text "One per line: cs.SD=Sound   cs.RO=Robotics   cs.AI=Artificial Intelligence" -style Sub.TLabel
grid .body.hint -row 4 -column 1 -columnspan 2 -sticky w -pady {0 12}
ttk::label .body.keywordlabel -text "Keywords → folders"
text .body.keywords -height 3 -width 50 -font {Monospace 11} -background white -foreground #1b2940 -relief solid -borderwidth 1 -padx 10 -pady 8 -undo 1
ttk::label .body.keywordhint -text "One per line: connectome=Connectome   or paste an arxiv.org/search URL (optionally followed by a folder name)" -style Sub.TLabel
grid .body.keywordlabel -row 5 -column 0 -sticky nw
grid .body.keywords -row 5 -column 1 -columnspan 2 -sticky ew -pady {0 4}
grid .body.keywordhint -row 6 -column 1 -columnspan 2 -sticky w -pady {0 12}
grid remove .body.keywordlabel .body.keywords .body.keywordhint
ttk::label .body.modelabel -text "Download mode"
grid .body.modelabel -row 7 -column 0 -sticky nw
ttk::frame .body.modes
grid .body.modes -row 7 -column 1 -columnspan 2 -sticky ew
foreach {value label} {limited "New papers per category" daily "Daily catch-up" all "All in selected categories"} {
    ttk::radiobutton .body.modes.$value -text $label -variable mode -value $value
    pack .body.modes.$value -anchor w -pady 3
    lappend activeControls .body.modes.$value
}
ttk::label .body.countlabel -text "New-paper count"
ttk::spinbox .body.count -from 1 -to 1000000 -textvariable limit -width 12
grid .body.countlabel -row 8 -column 0 -sticky w -pady 10
grid .body.count -row 8 -column 1 -sticky w -pady 10
ttk::label .body.contactlabel -text "Contact (optional)"
ttk::entry .body.contact -textvariable contact
grid .body.contactlabel -row 9 -column 0 -sticky w
grid .body.contact -row 9 -column 1 -columnspan 2 -sticky ew
ttk::label .body.dailyhint -text "Daily catch-up finds new submissions; install the daily timer from README for automatic runs.\nAll mode can take days. Completed PDFs survive Stop, restart, and network failures." -style Sub.TLabel
grid .body.dailyhint -row 10 -column 0 -columnspan 3 -sticky w -pady {16 10}
ttk::frame .body.actions
grid .body.actions -row 11 -column 0 -columnspan 3 -sticky ew
foreach {name label action} {start "Start downloads" start save "Save settings" save open "Open folder" open stop "Stop" stop} {
    ttk::button .body.actions.$name -text $label -command [list transmit $action]
    pack .body.actions.$name -side left -padx {0 8}
}
.body.actions.stop configure -state disabled
ttk::progressbar .body.progress -mode indeterminate
grid .body.progress -row 12 -column 0 -columnspan 3 -sticky ew -pady {14 8}
ttk::label .body.status -textvariable status -wraplength 820
grid .body.status -row 13 -column 0 -columnspan 3 -sticky w -pady {0 8}
text .body.log -height 7 -width 80 -font {Monospace 10} -background #eaf0f7 -foreground #24354b -relief flat -padx 10 -pady 8 -state disabled -wrap word
grid .body.log -row 14 -column 0 -columnspan 3 -sticky nsew
lappend activeControls .body.path .body.browse .body.subjects .body.keywords .body.count .body.contact .body.actions.start .body.actions.save .body.actions.open
fconfigure stdin -blocking 0 -encoding utf-8
fconfigure stdout -encoding utf-8 -buffering line
fileevent stdin readable receive
wm protocol . WM_DELETE_WINDOW closeWindow
