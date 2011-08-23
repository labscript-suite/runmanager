#!/usr/bin/env python

import gtk
import pango
import os


class Group(object):
    
    def __init__(self,name,filepath,notebook,vbox):
        self.name = name
        self.filepath = filepath
        
        self.builder = gtk.Builder()
        self.builder.add_from_file('interface.glade')
        self.toplevel = self.builder.get_object('tab_toplevel')
        notebook.append_page(self.toplevel, tab_label = gtk.Label(self.name))
                             
        self.checkbox = gtk.CheckButton(self.name)
        vbox.pack_start(self.checkbox,expand=False,fill=False)
        notebook.set_tab_reorderable(self.toplevel,True)
        
        notebook.show_all()
        notebook.set_current_page(-1)
        
        
        
class RunManager(object):
    def __init__(self):
        self.builder = gtk.Builder()
        self.builder.add_from_file('interface.glade')
        
        self.window = self.builder.get_object('window1')
        self.notebook = self.builder.get_object('notebook1')
        self.output_view = self.builder.get_object('textview1')
        self.output_buffer = self.output_view.get_buffer()
        self.use_globals_vbox = self.builder.get_object('use_globals_vbox')
        
        self.window.show_all()
        
        area=self.builder.get_object('drawingarea1')
        pixbuf=gtk.gdk.pixbuf_new_from_file(os.path.join('assets','grey.png'))
        pixmap, mask=pixbuf.render_pixmap_and_mask()
        area.window.set_back_pixmap(pixmap, False)
        self.output_view.modify_font(pango.FontDescription("monospace 10"))
        self.window.set_icon_from_file(os.path.join('assets','icon.png'))
        self.builder.connect_signals(self)
        
        
        self.groups = []
    
    def output(self,text):
        """Prints text to the output textbox"""
        print text,
        self.output_buffer.insert_at_cursor(text)
        # Automatically keep the textbox scrolled to the bottom:
        self.output_view.scroll_to_mark(app.output_buffer.get_insert(),0)
        # Make sure that GTK renders the text right away, without waiting
        # for callbacks to finish:
        while gtk.events_pending():
            gtk.main_iteration()
            
    def run(self):
        self.output('ready')
        gtk.main()
            
    def button_create_new_group(self,*args):
        entry_name = self.builder.get_object('entry_tabname')
        entry_path = self.builder.get_object('entry_tabfilepath')
        name = entry_name.get_text()
        filepath = entry_path.get_text()
        self.groups.append(Group(name,filepath,self.notebook,self.use_globals_vbox))
        entry_name.set_text('')
        entry_path.set_text('')
        
    def do_it(*args):
        self.output('do it')
         
app = RunManager()
app.run()
