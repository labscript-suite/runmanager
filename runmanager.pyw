#!/usr/bin/env python

import gtk
import pango

def output(text):
    """Prints text to the output textbox"""
    print text,
    app.output_buffer.insert_at_cursor(text)
    # Automatically keep the textbox scrolled to the bottom:
    app.output_view.scroll_to_mark(app.output_buffer.get_insert(),0)
    # Make sure that GTK renders the text right away, without waiting for callbacks to finish:
    while gtk.events_pending():
        gtk.main_iteration()

class RunManager(object):
    def __init__(self):
        builder = gtk.Builder()
        builder.add_from_file('interface.glade')
        self.window = builder.get_object('window1')
        self.window.show()
        builder.connect_signals(self)
        self.output_view = builder.get_object('textview1')
        self.output_buffer = self.output_view.get_buffer()
        self.output_view.modify_font(pango.FontDescription("monospace 10"))
        
    def run(self):
        output('\n\n\n\n\n\n\n\n\n\n\nready')
        gtk.main()
        
    def page_switched(self,notebook, page, page_num):
        if page_num == notebook.get_n_pages() - 2:
            print 'was plus one!'
            notebook.insert_page(gtk.Label('hello!'),position = notebook.get_n_pages() - 2)
            notebook.show_all()
    def do_it(*args):
        print 'do it'
         
app = RunManager()
app.run()
