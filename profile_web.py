"""Small, self-contained account UI shared by standalone and profiled portals."""
def markup():
    return '''<dialog id="passwordDialog" style="width:min(460px,calc(100vw - 24px));border:1px solid #dce3ee;border-radius:16px;padding:26px">
    <form id="passwordForm"><h2 style="margin-top:0">Смяна на парола</h2>
    <label>Текуща парола<input id="oldPassword" type="password" autocomplete="current-password" required></label>
    <label>Нова парола<input id="newPassword" type="password" autocomplete="new-password" required></label>
    <label>Повтори новата парола<input id="repeatPassword" type="password" autocomplete="new-password" required></label>
    <p id="passwordError" role="alert" style="color:#b42318"></p>
    <div style="display:flex;gap:10px;justify-content:flex-end;margin-top:20px"><button type="button" onclick="document.getElementById('passwordDialog').close()">Отказ</button><button id="passwordSave" class="primary">Запази паролата</button></div>
    </form></dialog>'''


def script():
    return r'''
    function openPassword(){const d=$('passwordDialog');$('passwordForm').reset();$('passwordError').textContent='';d.showModal();$('oldPassword').focus();}
    $('passwordForm').addEventListener('submit',async event=>{event.preventDefault();$('passwordError').textContent='';
      if($('newPassword').value!==$('repeatPassword').value){$('passwordError').textContent='Паролите не съвпадат.';return;}
      $('passwordSave').disabled=true;
      try{await apiFetch('/change-password',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({current_password:$('oldPassword').value,new_password:$('newPassword').value,confirmation:$('repeatPassword').value})});$('passwordForm').reset();window.location.replace(configuredBasePath+'/login');}
      catch(error){$('passwordError').textContent=error.message;}finally{$('passwordSave').disabled=false;}
    });
    '''
