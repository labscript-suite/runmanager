import gtk

class RunManager(object):
    def __init__(self):
        builder = gtk.Builder()
        builder.add_from_file('interface.glade')
        window = builder.get_object('window1')
        window.show()
        
    def run(self):
        gtk.main()
      
app = RunManager()
app.run()
