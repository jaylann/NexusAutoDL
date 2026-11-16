// Test-rig extension: makes Vortex show its free-download dialog and report
// where it drew the buttons.
//
// Vortex shows the dialog whenever its queue of "downloads a free user has to
// fetch from the website" is not empty (state.session.nexus.freeUserDLQueue).
// Putting one entry there opens the real dialog, drawn by Vortex itself,
// without a Nexus account: the mod details are placeholders ("Mod with id 1"),
// the buttons are the same. Nothing is downloaded and nothing is clicked.
// Works from Vortex 1.13 (the older orange dialog) to 2.x.
//
// The rig needs to know where the button is without asking the detector it is
// testing, so the extension writes the dialog's button rectangles (CSS pixels
// in the window, plus the device pixel ratio) to
// %NEXUSAUTODL_RIG_OUT%\vortex-dialog.json every two seconds.
//
// The mod and file ids below are placeholders; they only have to parse as an
// nxm link.
var fs = require('fs');
var path = require('path');

var DIALOG_DELAY_MS = 20000;
var REPORT_EVERY_MS = 2000;
var QUEUE_ENTRY = 'nxm://skyrimspecialedition/mods/1/files/1';
var DIALOG_ID = 'free-user-dl-dialog';
var REPORT_NAME = 'vortex-dialog.json';

// 'pending', 'done', or 'failed: <why>'.
var dispatched = 'pending';

function rectOf(element) {
  var rect = element.getBoundingClientRect();
  return { left: rect.left, top: rect.top, right: rect.right, bottom: rect.bottom };
}

function report(outDir) {
  // Vortex 2.x gives the dialog an id; 1.x does not, so fall back to whatever
  // modal is open. If that is some other dialog, its buttons and text tell
  // the rig what is in the way.
  var dialog = document.getElementById(DIALOG_ID);
  var modal = dialog || document.querySelector('.modal.in, .modal.show');
  var buttons = [];
  if (modal) {
    var found = modal.querySelectorAll('button');
    for (var i = 0; i < found.length; i++) {
      buttons.push({
        id: found[i].id || '',
        text: (found[i].textContent || '').trim(),
        rect: rectOf(found[i]),
      });
    }
  }
  var state = {
    time: Date.now(),
    dispatched: dispatched,
    devicePixelRatio: window.devicePixelRatio,
    inner: [window.innerWidth, window.innerHeight],
    dialogId: modal ? modal.id || '' : null,
    dialogText: modal ? (modal.textContent || '').slice(0, 300) : null,
    buttons: buttons,
  };
  // Written under another name first: the rig reads the file while Vortex runs.
  var target = path.join(outDir, REPORT_NAME);
  fs.writeFileSync(target + '.tmp', JSON.stringify(state));
  fs.renameSync(target + '.tmp', target);
}

function main(context) {
  context.once(function () {
    setTimeout(function () {
      try {
        context.api.store.dispatch({
          type: 'ADD_FREEUSER_DLITEM',
          payload: QUEUE_ENTRY,
        });
        dispatched = 'done';
      } catch (err) {
        dispatched = 'failed: ' + (err && err.message ? err.message : err);
      }
    }, DIALOG_DELAY_MS);

    var outDir = process.env.NEXUSAUTODL_RIG_OUT;
    if (outDir) {
      setInterval(function () {
        try {
          report(outDir);
        } catch (err) {
          // Reporting must never get in Vortex's way.
        }
      }, REPORT_EVERY_MS);
    }
  });
  return true;
}

module.exports = { default: main };
