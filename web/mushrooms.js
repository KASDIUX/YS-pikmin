'use strict';
// The external site's normal login and copy controls remain in its own browser.
// Importing a copied coordinate is a local map selection, never a position command.
bind('mushroom-open',()=>{
  $('mushroom-error').textContent='';
  $('mushroom-dialog').showModal();
  $('mushroom-coordinates').focus();
});
bind('mushroom-cancel',()=>$('mushroom-dialog').close());
$('mushroom-form').addEventListener('submit',event=>{
  event.preventDefault();
  try{
    const position=coordinate($('mushroom-coordinates').value);
    const name=$('mushroom-name').value.trim();
    setDrawing(false);
    select(position,true);
    $('mushroom-dialog').close();
    if(event.submitter?.value==='favorite'){
      openName('favorites');
      $('save-name').value=name||'外部菇點';
      const mushroom=document.querySelector('input[name="favorite-type"][value="mushroom"]');
      if(mushroom)mushroom.checked=true;
    }else{
      toast('已選定菇點。確認地圖後，再按「套用」。');
    }
  }catch(error){
    $('mushroom-error').textContent=error.message;
  }
});
