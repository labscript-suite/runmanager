import gtk

builder = gtk.Builder()
builder.add_from_file('bug.glade')
window = builder.get_object('window1')
window.show_all()
gtk.main()

