#!/usr/bin/env python

import gtk
import pango
import os

if os.name == 'nt':
    # Have Windows consider this program to be a separate app, and not
    # group it with other Python programs in the taskbar:
    import ctypes
    myappid = 'monashbec.labscript.runmanager.1-0' # arbitrary string
#    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(myappid)

class Group(object):
    
    def __init__(self,name,filepath,notebook,vbox):
        self.name = name
        self.filepath = filepath
        self.notebook = notebook
        self.vbox = vbox
        
        self.builder = gtk.Builder()
        self.builder.add_from_file('interface.glade')
        self.toplevel = self.builder.get_object('tab_toplevel')
        self.tab = gtk.HBox()
        
        #get a stock close button image
        close_image = gtk.image_new_from_stock(gtk.STOCK_CLOSE, gtk.ICON_SIZE_MENU)
        image_w, image_h = gtk.icon_size_lookup(gtk.ICON_SIZE_MENU)
        
        #make the close button
        btn = gtk.Button()
        btn.set_relief(gtk.RELIEF_NONE)
        btn.set_focus_on_click(False)
        btn.add(close_image)
        
        #this reduces the size of the button
        style = gtk.RcStyle()
        style.xthickness = 0
        style.ythickness = 0
        btn.modify_style(style)
        
        label = gtk.Label(self.name)
        label.set_ellipsize(pango.ELLIPSIZE_END)
        label.set_tooltip_text(self.name)
        self.tab.pack_start(label)
        self.tab.pack_start(btn, False, False)
        self.tab.show_all()
        notebook.append_page(self.toplevel, tab_label = self.tab)
#        notebook.set_tab_label_packing(self.toplevel, expand=True, fill=True, pack_type=gtk.PACK_START)        
                     
        self.checkbox = gtk.CheckButton(self.name)
        self.vbox.pack_start(self.checkbox,expand=False,fill=False)
        self.vbox.show_all()
        notebook.set_tab_reorderable(self.toplevel,True)
        
        notebook.show()

        #connect the close button
        btn.connect('clicked', self.on_closetab_button_clicked)

    def on_closetab_button_clicked(self, *args):
        #get the page number of the tab we wanted to close
        pagenum = self.notebook.page_num(self.toplevel)
        #and close it
        self.notebook.remove_page(pagenum)
        self.checkbox.destroy()
                
        
        
class RunManager(object):
    def __init__(self):
        self.builder = gtk.Builder()
        self.builder.add_from_file('interface.glade')
        
        self.window = self.builder.get_object('window1')
        self.notebook = self.builder.get_object('notebook1')
        self.output_view = self.builder.get_object('textview1')
        self.output_buffer = self.output_view.get_buffer()
        self.use_globals_vbox = self.builder.get_object('use_globals_vbox')
        self.grouplist_vbox = self.builder.get_object('grouplist_vbox')
        self.no_file_opened = self.builder.get_object('label_no_file_opened')
        self.chooser_h5_file = self.builder.get_object('chooser_h5_file')
        self.window.show_all()
        
        area=self.builder.get_object('drawingarea1')
        pixbuf=gtk.gdk.pixbuf_new_from_file(os.path.join('assets','grey.png'))
        pixmap, mask=pixbuf.render_pixmap_and_mask()
        area.window.set_back_pixmap(pixmap, False)
        self.output_view.modify_font(pango.FontDescription("monospace 10"))
        self.window.set_icon_from_file(os.path.join('assets','icon.png'))
        self.builder.get_object('filefilter1').add_pattern('*.h5')
        self.builder.get_object('filefilter2').add_pattern('*.py')
        self.grouplist_vbox.hide()
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
        filepath = self.chooser_h5_file.get_filenames()[0]
        self.groups.append(Group(name,filepath,self.notebook,self.use_globals_vbox))
        entry_name.set_text('')
    
    def on_file_chosen(self,chooser):
        self.grouplist_vbox.show()
        self.no_file_opened.hide()
        
    def on_selection_changed(self,chooser):
        if not self.chooser_h5_file.get_filenames():
            self.grouplist_vbox.hide()
            self.no_file_opened.show()
              
    def do_it(self,*args):
        self.output('do it')
         
app = RunManager()
app.run()
