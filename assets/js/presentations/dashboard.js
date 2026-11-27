import { $, api, element, message } from './common.js';

async function load() {
  try {
    const { shows, canCreate } = await api('shows');
    $('new-show').hidden = !canCreate;
    $('show-list').replaceChildren();
    for (const show of shows) {
      const card = element('article', undefined, 'panel');
      card.append(element('h2', show.title));
      if (show.programme) card.append(element('p', 'Concert programme · ' + show.programme.sets.length + ' linked presentations', 'hint'));
      for (const [label, href] of [...(show.canEdit ? [['Edit timeline', show.editUrl]] : []), ['Control', show.controlUrl]]) {
        const link = element('a', label, 'button');
        link.href = href;
        card.append(link, document.createTextNode(' '));
      }
      if (show.canEdit) {
      const duplicate = element('button', 'Duplicate');
      duplicate.onclick = async () => {
        try {
          const copied = await api('shows/' + show.id + '/duplicate', 'POST', {});
          location.href = copied.editUrl;
        } catch (error) { message('Could not duplicate: ' + error.message, true); }
      };
      card.append(duplicate);
      const remove = element('button', 'Delete', 'danger');
      remove.onclick = async () => {
        if (!confirm('Delete this presentation and end its broadcast? Uploaded library media will be kept.')) return;
        try {
          await api('shows/' + show.id, 'DELETE', { revision: show.revision });
          await load();
        } catch (error) { message(error.message, true); }
      };
      card.append(remove);
      }
      $('show-list').append(card);
    }
    message(shows.length ? '' : 'No presentations yet. Create your first show above.');
  } catch (error) { message('Could not load presentations: ' + error.message, true); }
}
$('new-show').onsubmit = async event => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  try {
    const show = await api('shows', 'POST', { title: $('new-title').value });
    location.href = show.editUrl;
  } catch (error) { message(error.message, true); button.disabled = false; }
};
load();
