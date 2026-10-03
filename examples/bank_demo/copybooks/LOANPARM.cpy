      *================================================================*
      * LOANPARM - PARAMETER BLOCK FOR CALL 'AMORT'                    *
      *================================================================*
       01  LOAN-PARMS.
           05 LP-PRINCIPAL         PIC 9(7)V99 COMP-3.
           05 LP-ANNUAL-RATE       PIC 9V9(5) COMP-3.
           05 LP-TERM-MONTHS       PIC 9(3).
           05 LP-PAYMENT           PIC 9(7)V99 COMP-3.
           05 LP-TOTAL-INTEREST    PIC 9(7)V99 COMP-3.
           05 LP-RETURN-CODE       PIC 99.
