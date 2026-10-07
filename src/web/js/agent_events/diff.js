/* Native unified patches and Claude structured hunks share this display model. */
(function(root) {
    'use strict';
    function text(patch) {
        if(typeof patch==='string')return patch;
        if(Array.isArray(patch))return patch.map(function(hunk) {
            return '@@ -'+hunk.oldStart+','+hunk.oldLines+' +'+hunk.newStart+','+hunk.newLines+' @@\n'+(hunk.lines||[]).join('\n');
        }).join('\n');
        return JSON.stringify(patch,null,2);
    }
    function lines(patch) {
        var before=0,after=0;
        return text(patch).split('\n').map(function(line) {
            var hunk=/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/.exec(line);
            if(hunk){before=Number(hunk[1]);after=Number(hunk[2]);return {type:'header',text:line};}
            if(line.startsWith('---')||line.startsWith('+++')||line.startsWith('diff ')||line.startsWith('index ')||!before&&!after)return {type:'header',text:line};
            if(line[0]==='-')return {type:'delete',before:before++,text:line.slice(1)};
            if(line[0]==='+')return {type:'add',after:after++,text:line.slice(1)};
            if(line[0]===' ')return {type:'context',before:before++,after:after++,text:line.slice(1)};
            return {type:'header',text:line};
        });
    }
    function render(host,patch) {
        host.replaceChildren();
        var button=document.createElement('button');button.type='button';button.textContent='Side by side';
        var table=document.createElement('table');table.className='cuttle-diff';
        var side=false;
        function paint() {
            table.replaceChildren();
            for(var line of lines(patch)) {
                var row=document.createElement('tr');row.className='diff-'+line.type;
                var values=side&&line.type!=='header'?[line.before||'',line.type==='add'?'':line.text,line.after||'',line.type==='delete'?'':line.text]:[line.before||'',line.after||'',line.text];
                values.forEach(function(value){var cell=document.createElement('td');cell.textContent=value;row.append(cell);});
                table.append(row);
            }
        }
        button.addEventListener('click',function(){side=!side;button.textContent=side?'Unified':'Side by side';paint();});
        host.append(button,table);paint();
    }
    root.CuttleEventDiff={text:text,lines:lines,render:render};
})(typeof window==='undefined'?globalThis:window);
